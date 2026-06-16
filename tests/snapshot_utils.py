from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_HEX_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _normalize_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if _UUID_RE.match(value):
        return "<uuid>"
    if _HEX_TOKEN_RE.match(value):
        return "<token>"
    if _ISO_DATETIME_RE.match(value):
        return "<datetime>"
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        suffix = f"?{parsed.query}" if parsed.query else ""
        return f"<url>{parsed.path}{suffix}"
    return value


def normalize_snapshot_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: normalize_snapshot_payload(payload[key])
            for key in sorted(payload.keys())
        }
    if isinstance(payload, list):
        return [normalize_snapshot_payload(item) for item in payload]
    return _normalize_scalar(payload)


def assert_matches_json_snapshot(
    request: Any,
    snapshot_name: str,
    payload: Any,
) -> None:
    normalized = normalize_snapshot_payload(payload)
    rendered = json.dumps(normalized, indent=2, ensure_ascii=True, sort_keys=True)
    snapshot_dir = Path(str(request.fspath)).parent / "__snapshots__"
    snapshot_dir.mkdir(exist_ok=True)
    snapshot_path = snapshot_dir / f"{snapshot_name}.json"
    if not snapshot_path.exists():
        snapshot_path.write_text(rendered + "\n", encoding="utf-8")
        raise AssertionError(
            f"Created missing snapshot {snapshot_path}. Re-run the test to verify it."
        )
    expected = snapshot_path.read_text(encoding="utf-8").strip()
    assert rendered == expected
