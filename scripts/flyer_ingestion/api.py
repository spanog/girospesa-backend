"""Authenticated HTTP adapter for the existing private flyer API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


class FlyerAgentApiError(RuntimeError):
    """Raised when a private flyer API request cannot complete safely."""


@dataclass(frozen=True)
class PreflightResult:
    """Read-only duplicate decision returned before any Storage write."""

    status: str
    file_hash: str
    upload_supermarket_ids: tuple[str, ...]


class FlyerAgentApi:
    """Adapter for the UI's normal preflight, upload and review endpoints."""

    def __init__(self, http: httpx.Client, api_url: str, access_token: str) -> None:
        self._http = http
        self._api_url = api_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def preflight(
        self,
        pdf: Path,
        supermarket_ids: tuple[str, ...],
        valid_from: str,
        valid_to: str,
    ) -> PreflightResult:
        with pdf.open("rb") as stream:
            files = _preflight_files(pdf, stream, supermarket_ids, valid_from, valid_to)
            payload = self._request("POST", "/flyers/ingestion-preflight", files=files)
        return _preflight_result(payload)

    def upload(self, pdf: Path, supermarket_ids: tuple[str, ...], valid_from: str, valid_to: str) -> dict[str, Any]:
        signed = self._request("POST", "/flyers/upload-url", json=_signed_upload_body(pdf, supermarket_ids))
        self._upload_to_storage(pdf, signed)
        return self._request("POST", "/flyers/upload/complete", json=_complete_upload_body(pdf, signed, supermarket_ids, valid_from, valid_to))

    def start_extraction(self, flyer_id: str) -> None:
        self._request("POST", f"/flyers/{flyer_id}/extract", json={})

    def flyer_status(self, flyer_id: str) -> str:
        payload = self._request("GET", f"/flyers/{flyer_id}")
        status = payload.get("status") if isinstance(payload, dict) else None
        if not isinstance(status, str):
            raise FlyerAgentApiError("Flyer status response is invalid")
        return status

    def draft_count(self, flyer_id: str) -> int:
        payload = self._request("GET", f"/flyers/{flyer_id}/draft-offers")
        if not isinstance(payload, list):
            raise FlyerAgentApiError("Draft offers response is invalid")
        return len(payload)

    def confirm_offers(self, flyer_id: str) -> None:
        self._request("POST", f"/flyers/{flyer_id}/offers/confirm", json={})

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, f"{self._api_url}{path}", headers=self._headers, **kwargs)
        if response.is_error:
            raise FlyerAgentApiError(_error_message(response))
        return response.json()

    def _upload_to_storage(self, pdf: Path, signed: dict[str, Any]) -> None:
        url = _signed_storage_url(signed)
        response = self._http.put(url, content=pdf.read_bytes(), headers=_storage_headers())
        if response.is_error:
            raise FlyerAgentApiError(_error_message(response))


def _preflight_files(
    pdf: Path,
    stream: Any,
    ids: tuple[str, ...],
    valid_from: str,
    valid_to: str,
) -> list[tuple[str, Any]]:
    targets = [("supermarket_ids", (None, value)) for value in ids]
    return [
        ("file", (pdf.name, stream, "application/pdf")),
        *targets,
        ("valid_from", (None, valid_from)),
        ("valid_to", (None, valid_to)),
    ]


def _preflight_result(payload: Any) -> PreflightResult:
    if not isinstance(payload, dict):
        raise FlyerAgentApiError("Preflight response is invalid")
    status = payload.get("status")
    file_hash = payload.get("file_hash")
    upload_ids = payload.get("upload_supermarket_ids")
    if not isinstance(status, str) or not isinstance(file_hash, str) or not isinstance(upload_ids, list):
        raise FlyerAgentApiError("Preflight response is incomplete")
    return PreflightResult(status, file_hash, tuple(str(value) for value in upload_ids))


def _signed_upload_body(pdf: Path, ids: tuple[str, ...]) -> dict[str, Any]:
    return {"file_name": pdf.name, "content_type": "application/pdf", "size_bytes": pdf.stat().st_size, "supermarket_ids": list(ids)}


def _complete_upload_body(pdf: Path, signed: dict[str, Any], ids: tuple[str, ...], valid_from: str, valid_to: str) -> dict[str, Any]:
    path = signed.get("path")
    if not isinstance(path, str) or not path:
        raise FlyerAgentApiError("Signed upload response is missing its path")
    return {
        "storage_path": path,
        "file_name": pdf.name,
        "content_type": "application/pdf",
        "supermarket_ids": list(ids),
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


def _signed_storage_url(signed: dict[str, Any]) -> str:
    url, token = signed.get("signed_url"), signed.get("token")
    if not isinstance(url, str) or not isinstance(token, str) or not url or not token:
        raise FlyerAgentApiError("Signed upload response is incomplete")
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.setdefault("token", token)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _storage_headers() -> dict[str, str]:
    return {"content-type": "application/pdf", "cache-control": "max-age=3600", "x-upsert": "false"}


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    return f"Flyer API request failed ({response.status_code}): {detail}"
