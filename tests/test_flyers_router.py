"""Unit tests for api/routers/flyers.py — upload admin/manager gating + validation.

Tests verify that:
- Upload requires admin or manager role
- Manager cannot set is_public=True
- Manager auto-fills supermarket_id from profile
- Manager cannot upload for a different supermarket
- File type and size validation are enforced
- Duplicate hash+supermarket returns 409

Infrastructure modules (supabase, jose, etc.) are stubbed so tests run
without a venv or external services.
"""

from __future__ import annotations

import sys
import os
import io
import types
from typing import Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub infrastructure modules not available without a full venv
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_settings_obj = MagicMock()
_settings_obj.llm_provider = "gemini"
_settings_obj.google_api_key = ""
_settings_obj.gemini_model = "gemma-4-31b-it"
_config_mod.settings = _settings_obj  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod

sys.modules["core.database"] = MagicMock()
sys.modules["services.geocoding"] = MagicMock()
for _svc_mod in ("services.extraction.service", "services.extraction", "services.extraction.providers"):
    sys.modules.pop(_svc_mod, None)

# ---------------------------------------------------------------------------
# Stub core.auth: provide real-looking dependencies for testing
# ---------------------------------------------------------------------------
from fastapi import HTTPException

_auth_mod = types.ModuleType("core.auth")

_auth_mod.get_current_user_id = MagicMock()  # type: ignore[attr-defined]
_auth_mod.get_current_user = MagicMock()  # type: ignore[attr-defined]
_auth_mod.require_admin_or_manager = MagicMock()  # type: ignore[attr-defined]


def _assert_flyer_access_real(profile: dict, flyer: dict) -> None:
    if profile.get("role") != "supermarket_manager":
        return
    managed = profile.get("managed_supermarket_id")
    if flyer.get("supermarket_id") != managed:
        raise HTTPException(
            status_code=403,
            detail="Access denied: flyer belongs to a different supermarket",
        )


_auth_mod.assert_flyer_access = _assert_flyer_access_real  # type: ignore[attr-defined]
_auth_mod.get_optional_user_id = MagicMock()  # type: ignore[attr-defined]


def _require_admin_real(user: dict) -> dict:
    role = user.get("app_metadata", {}).get("role")
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


_auth_mod.require_admin = _require_admin_real  # type: ignore[attr-defined]
sys.modules["core.auth"] = _auth_mod

from fastapi import FastAPI
import httpx
import pytest

import api.routers.flyers as _flyers_module
from api.routers.flyers import router

_DEP_GET_USER_ID = _flyers_module.get_current_user_id
_DEP_PROFILE = _flyers_module.require_admin_or_manager

# ---------------------------------------------------------------------------
# Build a minimal test app
# ---------------------------------------------------------------------------
test_app = FastAPI()
test_app.include_router(router, prefix="/flyers")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ADMIN_PROFILE = {"id": "admin-456", "role": "admin", "managed_supermarket_id": None}
MANAGER_PROFILE = {"id": "mgr-123", "role": "supermarket_manager", "managed_supermarket_id": "sup-1"}

_SMALL_PDF = b"%PDF-1.4 fake pdf content"


def _make_upload_file(
    content: bytes = _SMALL_PDF,
    content_type: str = "application/pdf",
    filename: str = "test.pdf",
) -> tuple:
    return ("file", (filename, content, content_type))


def _mock_supabase_for_upload(insert_return: Optional[dict] = None) -> MagicMock:
    """Return a mock Supabase client that simulates Storage upload + table insert."""
    sb = MagicMock()
    sb.storage.from_.return_value.upload.return_value = MagicMock()
    sb.storage.from_.return_value.get_public_url.return_value = "https://storage.example.com/flyers/test.pdf"
    row_data = insert_return or {
        "id": "flyer-uuid",
        "user_id": "admin-456",
        "status": "pending",
        "is_public": False,
    }
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[row_data])
    return sb


async def _post_upload(dep_overrides: dict, files: list, data: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/flyers/upload", files=files, data=data or {})


# ---------------------------------------------------------------------------
# Tests — is_public admin gating
# ---------------------------------------------------------------------------


class TestUploadFlyerIsPublic:
    @pytest.mark.asyncio
    async def test_admin_upload_is_public_false_by_default(self):
        """Admin upload creates a private flyer (is_public=False) by default."""
        sb = _mock_supabase_for_upload({"id": "f1", "is_public": False, "status": "pending", "user_id": "admin-456"})

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
                [_make_upload_file()],
            )

        assert resp.status_code == 201
        insert_call_kwargs = sb.table.return_value.insert.call_args[0][0]
        assert insert_call_kwargs["is_public"] is False

    @pytest.mark.asyncio
    async def test_admin_can_set_is_public_true(self):
        """An admin user can upload a public flyer (is_public=True)."""
        sb = _mock_supabase_for_upload({"id": "f2", "is_public": True, "status": "pending", "user_id": "admin-456"})

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
                [_make_upload_file()],
                data={"is_public": "true"},
            )

        assert resp.status_code == 201
        insert_call_kwargs = sb.table.return_value.insert.call_args[0][0]
        assert insert_call_kwargs["is_public"] is True

    @pytest.mark.asyncio
    async def test_manager_cannot_set_is_public_true(self):
        """A manager requesting is_public=True receives 403 Forbidden."""
        sb = _mock_supabase_for_upload()

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "mgr-123", _DEP_PROFILE: lambda: MANAGER_PROFILE},
                [_make_upload_file()],
                data={"is_public": "true"},
            )

        assert resp.status_code == 403
        assert "Managers" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_manager_auto_fills_supermarket_id(self):
        """Manager upload without supermarket_id uses managed_supermarket_id."""
        sb = _mock_supabase_for_upload({"id": "f3", "is_public": False, "status": "pending", "user_id": "mgr-123"})

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "mgr-123", _DEP_PROFILE: lambda: MANAGER_PROFILE},
                [_make_upload_file()],
            )

        assert resp.status_code == 201
        insert_call_kwargs = sb.table.return_value.insert.call_args[0][0]
        assert insert_call_kwargs["supermarket_id"] == "sup-1"

    @pytest.mark.asyncio
    async def test_manager_wrong_supermarket_id_403(self):
        """Manager providing a different supermarket_id receives 403."""
        sb = _mock_supabase_for_upload()

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "mgr-123", _DEP_PROFILE: lambda: MANAGER_PROFILE},
                [_make_upload_file()],
                data={"supermarket_id": "sup-other"},
            )

        assert resp.status_code == 403


class TestUploadFlyerValidation:
    @pytest.mark.asyncio
    async def test_unsupported_content_type_rejected(self):
        """Files with unsupported MIME types return 422."""
        resp = await _post_upload(
            {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
            [_make_upload_file(content=b"data", content_type="text/plain", filename="test.txt")],
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_oversized_file_rejected(self):
        """Files exceeding 50 MB return 413."""
        large_content = b"x" * (50 * 1024 * 1024 + 1)
        resp = await _post_upload(
            {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
            [_make_upload_file(content=large_content)],
        )
        assert resp.status_code == 413


class TestUploadFlyerDuplicate:
    @pytest.mark.asyncio
    async def test_duplicate_hash_and_supermarket_returns_409(self):
        """Uploading the same file+supermarket twice returns 409 Conflict."""
        sb = MagicMock()
        sb.storage.from_.return_value.upload.return_value = MagicMock()
        sb.storage.from_.return_value.get_public_url.return_value = "https://storage.example.com/flyers/dup.pdf"
        existing_response = MagicMock()
        existing_response.data = {"id": "existing-flyer-uuid"}
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = existing_response

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
                [_make_upload_file()],
                data={"supermarket_name": "Esselunga"},
            )

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_no_duplicate_check_without_supermarket_name(self):
        """When supermarket_name is absent the duplicate check is skipped entirely."""
        sb = _mock_supabase_for_upload()

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
                [_make_upload_file()],
            )

        assert resp.status_code == 201
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.assert_not_called()
