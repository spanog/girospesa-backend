"""Unit tests for api/routers/flyer_requests.py.

Tests verify:
- Valid payload → 201 with { id, status: 'pending' }
- Missing required city field → 422 validation error
- Notes > 500 chars → 422 validation error
- Resend failure does NOT cause a 500 — DB record is the safety net
- Guest (no auth) is accepted (no 401)
"""

from __future__ import annotations

import sys
import os
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub infrastructure modules
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders", "resend"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_settings = MagicMock()
_settings.resend_api_key = "re_test"
_settings.admin_notification_email = "admin@example.com"
_config_mod.settings = _settings  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod

_db_mod = types.ModuleType("core.database")
_db_mod.get_supabase = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.database"] = _db_mod

# ---------------------------------------------------------------------------
# Stub core.auth: provide get_optional_user_id
# ---------------------------------------------------------------------------
_auth_mod = types.ModuleType("core.auth")
_auth_mod.get_optional_user_id = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.auth"] = _auth_mod

# ---------------------------------------------------------------------------
# Build a minimal test app
# ---------------------------------------------------------------------------
from fastapi import FastAPI
import httpx
import pytest

import api.routers.flyer_requests as _module
from api.routers.flyer_requests import router

test_app = FastAPI()
test_app.include_router(router, prefix="/flyer-requests")

_DEP_OPTIONAL_USER = _module.get_optional_user_id  # stable ref for dependency_overrides


def _make_supabase_mock(record_id: str = "test-uuid-1234") -> MagicMock:
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": record_id, "status": "pending"}
    ]
    return sb


async def _post(url: str, dep_overrides: dict, json: dict) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, json=json)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_request_returns_201():
    """A well-formed payload should return 201 with id and status."""
    mock_sb = _make_supabase_mock()
    with patch.object(_module, "get_supabase", return_value=mock_sb):
        resp = await _post(
            "/flyer-requests",
            {_DEP_OPTIONAL_USER: lambda: "user-abc"},
            json={"city": "Milano"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "test-uuid-1234"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_guest_can_submit():
    """No auth token (guest) should be accepted — no 401."""
    mock_sb = _make_supabase_mock()
    with patch.object(_module, "get_supabase", return_value=mock_sb):
        resp = await _post(
            "/flyer-requests",
            {_DEP_OPTIONAL_USER: lambda: None},
            json={"city": "Roma", "supermarket": "Lidl"},
        )

    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_missing_city_returns_422():
    """City is required; omitting it must yield 422."""
    resp = await _post(
        "/flyer-requests",
        {_DEP_OPTIONAL_USER: lambda: None},
        json={"notes": "senza città"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_notes_too_long_returns_422():
    """Notes exceeding 500 characters must yield 422."""
    resp = await _post(
        "/flyer-requests",
        {_DEP_OPTIONAL_USER: lambda: None},
        json={"city": "Napoli", "notes": "x" * 501},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_resend_failure_does_not_cause_500():
    """If Resend raises, the endpoint must still return 201 (DB is the safety net)."""
    mock_sb = _make_supabase_mock()

    with patch.object(_module, "get_supabase", return_value=mock_sb):
        with patch.object(_module, "_send_admin_notification", side_effect=Exception("Resend down")):
            resp = await _post(
                "/flyer-requests",
                {_DEP_OPTIONAL_USER: lambda: "user-xyz"},
                json={"city": "Torino"},
            )

    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_full_payload_accepted():
    """All optional fields provided should be stored and return 201."""
    mock_sb = _make_supabase_mock("full-uuid-5678")
    payload = {
        "city": "Firenze",
        "supermarket": "Esselunga",
        "flyer_url": "https://www.esselunga.it/volantino",
        "notes": "Volantino settimanale",
        "email": "utente@example.com",
    }

    with patch.object(_module, "get_supabase", return_value=mock_sb):
        resp = await _post(
            "/flyer-requests",
            {_DEP_OPTIONAL_USER: lambda: "user-123"},
            json=payload,
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == "full-uuid-5678"
