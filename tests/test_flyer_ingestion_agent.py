from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

from scripts.flyer_ingestion import agent_auth
from scripts.flyer_ingestion.agent_auth import (
    FlyerAgentAuthSettings,
    FlyerAgentCredentials,
    FlyerAgentProvisioner,
    SupabaseFlyerAgentAuthenticator,
)
from scripts.flyer_ingestion.api import FlyerAgentApi, PreflightResult
from scripts.flyer_ingestion.runner import AgentIngestionRequest, FlyerAgentRunner


def test_agent_auth_uses_the_technical_account_without_server_credentials():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["apikey"] = request.headers["apikey"]
        return httpx.Response(200, json={"access_token": "bot-token"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        token = SupabaseFlyerAgentAuthenticator(http).access_token(
            FlyerAgentAuthSettings("https://api.test", "https://supabase.test", "public-key"),
            FlyerAgentCredentials("bot@test", "password"),
        )

    assert token == "bot-token"
    assert seen == {
        "url": "https://supabase.test/auth/v1/token?grant_type=password",
        "apikey": "public-key",
    }


def test_provisioner_creates_a_separate_admin_and_keeps_its_secret_in_keychain(monkeypatch):
    store = MagicMock()
    store.load.return_value = None
    supabase = MagicMock()
    monkeypatch.setattr(agent_auth, "get_supabase", lambda: supabase)
    monkeypatch.setattr(
        agent_auth,
        "seed_admin_user",
        lambda _client, _seed: SimpleNamespace(user_id="bot-user"),
    )

    user_id = FlyerAgentProvisioner(store).provision("volantini-bot@test")

    assert user_id == "bot-user"
    assert store.save.call_count == 1
    update = supabase.auth.admin.update_user_by_id.call_args.args[1]
    assert update["app_metadata"] == {"role": "admin"}


def test_api_uses_preflight_then_signed_storage_then_completion(tmp_path: Path):
    pdf = _pdf(tmp_path)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/flyers/ingestion-preflight":
            return httpx.Response(200, json=_preflight("new", ["store-1"]))
        if request.url.path == "/flyers/upload-url":
            return httpx.Response(200, json=_signed_upload())
        if request.url.host == "storage.test":
            return httpx.Response(200, json={"Key": "path.pdf"})
        return httpx.Response(201, json={"id": "flyer-1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        api = FlyerAgentApi(http, "https://api.test", "bot-token")
        preflight = api.preflight(pdf, ("store-1",), "2026-09-24", "2026-10-04")
        flyer = api.upload(pdf, preflight.upload_supermarket_ids, "2026-09-24", "2026-10-04")

    assert preflight.status == "new"
    assert flyer["id"] == "flyer-1"
    assert [request.url.path for request in requests] == [
        "/flyers/ingestion-preflight",
        "/flyers/upload-url",
        "/object/upload/sign/flyers/path.pdf",
        "/flyers/upload/complete",
    ]
    assert requests[2].url.params["token"] == "signed-token"
    assert requests[2].content == pdf.read_bytes()


def test_known_candidate_stops_before_upload_and_removes_processed_pdf(tmp_path: Path, monkeypatch):
    pdf = _pdf(tmp_path)
    api = _FakeApi(PreflightResult("known", "a" * 64, ()))
    records: list[dict] = []
    monkeypatch.setattr("scripts.flyer_ingestion.runner.record_result", lambda *args, **kwargs: records.append(kwargs))

    result = FlyerAgentRunner(api, sleeper=lambda _: None).run(_request(pdf))

    assert result.status == "known"
    assert result.pdf_deleted is True
    assert not pdf.exists()
    assert api.upload_ids is None
    assert records[0]["status"] == "known"


def test_partial_candidate_uploads_only_missing_targets_retries_and_confirms(tmp_path: Path, monkeypatch):
    pdf = _pdf(tmp_path)
    api = _FakeApi(PreflightResult("partial", "b" * 64, ("store-2",)), statuses=["error", "done"])
    monkeypatch.setattr("scripts.flyer_ingestion.runner.record_result", lambda *args, **kwargs: None)
    waits: list[float] = []

    result = FlyerAgentRunner(api, sleeper=waits.append).run(_request(pdf))

    assert result.status == "done"
    assert result.pdf_deleted is True
    assert api.upload_ids == ("store-2",)
    assert api.extraction_starts == 2
    assert api.confirmed is True
    assert waits == [2]


def test_zero_drafts_preserves_pdf_and_does_not_confirm(tmp_path: Path, monkeypatch):
    pdf = _pdf(tmp_path)
    api = _FakeApi(PreflightResult("new", "c" * 64, ("store-1",)), draft_count=0)
    monkeypatch.setattr("scripts.flyer_ingestion.runner.record_result", lambda *args, **kwargs: None)

    result = FlyerAgentRunner(api, sleeper=lambda _: None).run(_request(pdf))

    assert result.status == "error"
    assert result.pdf_deleted is False
    assert pdf.exists()
    assert api.confirmed is False


def test_resume_confirms_a_completed_flyer_without_uploading_again(tmp_path: Path, monkeypatch):
    pdf = _pdf(tmp_path)
    api = _FakeApi(PreflightResult("new", "d" * 64, ()), statuses=["done"])
    monkeypatch.setattr("scripts.flyer_ingestion.runner.record_result", lambda *args, **kwargs: None)

    result = FlyerAgentRunner(api, sleeper=lambda _: None).resume(_request(pdf), "d" * 64, "flyer-1")

    assert result.status == "done"
    assert result.pdf_deleted is True
    assert api.upload_ids is None
    assert api.extraction_starts == 0
    assert api.confirmed is True


class _FakeApi:
    def __init__(self, preflight: PreflightResult, statuses: list[str] | None = None, draft_count: int = 1) -> None:
        self._preflight = preflight
        self._statuses = statuses or ["done"]
        self._draft_count = draft_count
        self.upload_ids: tuple[str, ...] | None = None
        self.extraction_starts = 0
        self.confirmed = False

    def preflight(self, *args) -> PreflightResult:
        return self._preflight

    def upload(self, pdf: Path, ids: tuple[str, ...], *args) -> dict[str, str]:
        self.upload_ids = ids
        return {"id": "flyer-1"}

    def start_extraction(self, flyer_id: str) -> None:
        self.extraction_starts += 1

    def flyer_status(self, flyer_id: str) -> str:
        return self._statuses.pop(0)

    def draft_count(self, flyer_id: str) -> int:
        return self._draft_count

    def confirm_offers(self, flyer_id: str) -> None:
        self.confirmed = True


def _pdf(tmp_path: Path) -> Path:
    path = tmp_path / "candidate.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    return path


def _preflight(status: str, upload_ids: list[str]) -> dict[str, object]:
    return {"status": status, "file_hash": "a" * 64, "upload_supermarket_ids": upload_ids}


def _signed_upload() -> dict[str, str]:
    return {"path": "user/flyers/path.pdf", "token": "signed-token", "signed_url": "https://storage.test/object/upload/sign/flyers/path.pdf"}


def _request(pdf: Path) -> AgentIngestionRequest:
    return AgentIngestionRequest("source", pdf, ("store-1", "store-2"), "2026-09-24", "2026-10-04")
