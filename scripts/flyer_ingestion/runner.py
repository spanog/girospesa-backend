"""Autonomous, stop-safe execution of one acquired flyer PDF."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time

from scripts.flyer_ingestion.api import FlyerAgentApi, FlyerAgentApiError, PreflightResult
from scripts.flyer_ingestion.state import default_state_path, record_result
from scripts.flyer_ingestion.workflow import FlyerIngestionPolicy


@dataclass(frozen=True)
class AgentIngestionRequest:
    """A locally validated source PDF and the source configuration it belongs to."""

    source_id: str
    pdf_path: Path
    supermarket_ids: tuple[str, ...]
    valid_from: str
    valid_to: str


@dataclass(frozen=True)
class AgentIngestionResult:
    """A terminal result suitable for the state audit and morning report."""

    status: str
    fingerprint: str
    flyer_id: str | None
    pdf_deleted: bool


class FlyerAgentRunner:
    """Run preflight, upload, extraction and confirmation without web UI actions."""

    def __init__(
        self,
        api: FlyerAgentApi,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 5,
        max_polls: int = 180,
    ) -> None:
        self._api = api
        self._sleeper = sleeper
        self._poll_interval_seconds = poll_interval_seconds
        self._max_polls = max_polls
        self._policy = FlyerIngestionPolicy()

    def run(self, request: AgentIngestionRequest) -> AgentIngestionResult:
        preflight = self._api.preflight(request.pdf_path, request.supermarket_ids, request.valid_from, request.valid_to)
        if preflight.status == "known":
            return self._finish(request, preflight, "known", None)
        if preflight.status not in {"new", "partial"}:
            return self._finish(request, preflight, "indeterminate", None)
        flyer = self._api.upload(request.pdf_path, preflight.upload_supermarket_ids, request.valid_from, request.valid_to)
        flyer_id = _flyer_id(flyer)
        return self._extract_and_confirm(request, preflight, flyer_id)

    def resume(self, request: AgentIngestionRequest, fingerprint: str, flyer_id: str) -> AgentIngestionResult:
        preflight = PreflightResult("new", fingerprint, ())
        status = self._api.flyer_status(flyer_id)
        if status == "done":
            return self._confirm_or_stop(request, preflight, flyer_id)
        if status == "error":
            return self._retry_after_error(request, preflight, flyer_id, retries_completed=0)
        if status == "pending":
            self._api.start_extraction(flyer_id)
        return self._wait_and_retry(request, preflight, flyer_id, retries_completed=0)

    def _extract_and_confirm(self, request: AgentIngestionRequest, preflight: PreflightResult, flyer_id: str) -> AgentIngestionResult:
        self._api.start_extraction(flyer_id)
        return self._wait_and_retry(request, preflight, flyer_id, retries_completed=0)

    def _wait_and_retry(self, request: AgentIngestionRequest, preflight: PreflightResult, flyer_id: str, retries_completed: int) -> AgentIngestionResult:
        while True:
            status = self._wait_for_terminal_status(flyer_id)
            if status == "done":
                return self._confirm_or_stop(request, preflight, flyer_id)
            return self._retry_after_error(request, preflight, flyer_id, retries_completed)

    def _retry_after_error(self, request: AgentIngestionRequest, preflight: PreflightResult, flyer_id: str, retries_completed: int) -> AgentIngestionResult:
        delay = self._policy.retry_delay_seconds(retries_completed)
        if delay is None:
            return self._finish(request, preflight, "error", flyer_id)
        self._sleeper(delay)
        self._api.start_extraction(flyer_id)
        return self._wait_and_retry(request, preflight, flyer_id, retries_completed + 1)

    def _wait_for_terminal_status(self, flyer_id: str) -> str:
        for _ in range(self._max_polls):
            status = self._api.flyer_status(flyer_id)
            if status in {"done", "error"}:
                return status
            self._sleeper(self._poll_interval_seconds)
        raise FlyerAgentApiError("Flyer extraction polling timed out")

    def _confirm_or_stop(self, request: AgentIngestionRequest, preflight: PreflightResult, flyer_id: str) -> AgentIngestionResult:
        if self._api.draft_count(flyer_id) < 1:
            return self._finish(request, preflight, "error", flyer_id)
        self._api.confirm_offers(flyer_id)
        return self._finish(request, preflight, "done", flyer_id)

    def _finish(self, request: AgentIngestionRequest, preflight: PreflightResult, status: str, flyer_id: str | None) -> AgentIngestionResult:
        record_result(default_state_path(), source_id=request.source_id, fingerprint=preflight.file_hash, status=status, flyer_id=flyer_id)
        deleted = _delete_processed_pdf(request.pdf_path, status)
        return AgentIngestionResult(status, preflight.file_hash, flyer_id, deleted)


def _flyer_id(flyer: object) -> str:
    value = flyer.get("id") if isinstance(flyer, dict) else None
    if not isinstance(value, str) or not value:
        raise FlyerAgentApiError("Flyer upload response has no id")
    return value


def _delete_processed_pdf(pdf_path: Path, status: str) -> bool:
    if status not in {"known", "done"}:
        return False
    pdf_path.unlink(missing_ok=True)
    return True
