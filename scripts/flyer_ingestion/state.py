"""Minimal local audit state for Codex flyer ingestion runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path


class FlyerIngestionStateError(ValueError):
    """Raised when the local skill state cannot be loaded safely."""


@dataclass(frozen=True)
class IngestionRecord:
    """A completed or stopped source-document attempt."""

    source_id: str
    fingerprint: str
    status: str
    flyer_id: str | None
    updated_at: str


def default_state_path() -> Path:
    """Return the user-local state path without embedding credentials in the repo."""
    return Path.home() / ".codex" / "state" / "girospesa-volantini.json"


def load_records(path: Path) -> dict[str, IngestionRecord]:
    """Return an empty state for a first run, otherwise validate prior records."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FlyerIngestionStateError("Local flyer-ingestion state is unreadable") from exc
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        raise FlyerIngestionStateError("Local flyer-ingestion state has no records mapping")
    return {key: _record_from_dict(key, value) for key, value in records.items()}


def record_result(
    path: Path,
    *,
    source_id: str,
    fingerprint: str,
    status: str,
    flyer_id: str | None,
) -> IngestionRecord:
    """Persist one result atomically so later runs retain a local audit trail."""
    record = IngestionRecord(source_id, fingerprint, status, flyer_id, _utc_timestamp())
    records = load_records(path)
    records[_record_key(source_id, fingerprint)] = record
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(_serialized(records), encoding="utf-8")
    temporary_path.replace(path)
    return record


def _record_from_dict(key: str, value: object) -> IngestionRecord:
    if not isinstance(value, dict):
        raise FlyerIngestionStateError(f"State record {key} is invalid")
    values = (value.get("source_id"), value.get("fingerprint"), value.get("status"), value.get("updated_at"))
    if not all(isinstance(item, str) and item for item in values):
        raise FlyerIngestionStateError(f"State record {key} is incomplete")
    flyer_id = value.get("flyer_id")
    if flyer_id is not None and not isinstance(flyer_id, str):
        raise FlyerIngestionStateError(f"State record {key} has an invalid flyer id")
    return IngestionRecord(values[0], values[1], values[2], flyer_id, values[3])


def _record_key(source_id: str, fingerprint: str) -> str:
    return f"{source_id}:{fingerprint}"


def _serialized(records: dict[str, IngestionRecord]) -> str:
    payload = {"records": {key: asdict(record) for key, record in sorted(records.items())}}
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
