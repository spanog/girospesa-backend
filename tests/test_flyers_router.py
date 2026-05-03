"""Unit tests for api/routers/flyers.py — upload admin/manager gating + validation.

Tests verify that:
- Upload requires admin or manager role
- Upload always creates private flyers
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


async def _get(url: str, dep_overrides: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides or {}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


# ---------------------------------------------------------------------------
# Tests — upload privacy
# ---------------------------------------------------------------------------


class TestUploadFlyerPrivacy:
    @pytest.mark.asyncio
    async def test_admin_upload_creates_private_flyer(self):
        """Admin upload creates a private flyer (is_public=False)."""
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
    async def test_upload_ignores_is_public_form_field(self):
        """Upload endpoint ignores any submitted is_public field and keeps flyer private."""
        sb = _mock_supabase_for_upload({"id": "f2", "is_public": False, "status": "pending", "user_id": "admin-456"})

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
                [_make_upload_file()],
                data={"is_public": "true"},
            )

        assert resp.status_code == 201
        insert_call_kwargs = sb.table.return_value.insert.call_args[0][0]
        assert insert_call_kwargs["is_public"] is False

    @pytest.mark.asyncio
    async def test_manager_auto_fills_supermarket_id(self):
        """Manager upload without supermarket_id uses managed_supermarket_id."""
        sb = _mock_supabase_for_upload({"id": "f3", "is_public": False, "status": "pending", "user_id": "mgr-123"})
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"name": "Manager Market"}
        )

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "mgr-123", _DEP_PROFILE: lambda: MANAGER_PROFILE},
                [_make_upload_file()],
            )

        assert resp.status_code == 201
        insert_call_kwargs = sb.table.return_value.insert.call_args[0][0]
        assert insert_call_kwargs["supermarket_id"] == "sup-1"
        assert insert_call_kwargs["supermarket_name"] == "Manager Market"

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


class TestPublicFlyersVisibility:
    @pytest.mark.asyncio
    async def test_public_list_excludes_unconfirmed_flyers(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_result = MagicMock()
        flyers_result.data = [
            {"id": "flyer-hidden", "status": "done", "is_public": True},
            {"id": "flyer-visible", "status": "done", "is_public": True},
        ]
        flyers_table.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = (
            flyers_result
        )

        offers_table = MagicMock()
        confirmed_result = MagicMock()
        confirmed_result.data = [{"flyer_id": "flyer-visible"}]
        offers_table.select.return_value.in_.return_value.eq.return_value.execute.return_value = confirmed_result

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                return flyers_table
            return offers_table

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get("/flyers/public")

        assert resp.status_code == 200
        assert resp.json() == [
            {
                "id": "flyer-visible",
                "status": "done",
                "is_public": True,
                "confirmed_count": 1,
            }
        ]

    @pytest.mark.asyncio
    async def test_public_list_excludes_future_start_flyers(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_result = MagicMock()
        flyers_result.data = [
            {"id": "flyer-future", "status": "done", "is_public": True},
            {"id": "flyer-visible", "status": "done", "is_public": True},
        ]
        flyers_table.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = (
            flyers_result
        )

        offers_table = MagicMock()
        confirmed_result = MagicMock()
        confirmed_result.data = [
            {"flyer_id": "flyer-visible"},
        ]
        offers_table.select.return_value.in_.return_value.eq.return_value.execute.return_value = confirmed_result

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                return flyers_table
            return offers_table

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get("/flyers/public")

        assert resp.status_code == 200
        assert resp.json() == [
            {
                "id": "flyer-visible",
                "status": "done",
                "is_public": True,
                "confirmed_count": 1,
            }
        ]

    @pytest.mark.asyncio
    async def test_guest_download_requires_confirmed_public_flyer(self):
        sb = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {
            "id": "flyer-1",
            "status": "done",
            "is_public": True,
            "supermarket_id": "sup-1",
        }

        flyers_table = MagicMock()
        flyers_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
            flyer_result
        )

        offers_table = MagicMock()
        count_result = MagicMock()
        count_result.count = 0
        offers_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = count_result

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                return flyers_table
            return offers_table

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get("/flyers/flyer-1/download", {_flyers_module.get_optional_user_id: lambda: None})

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Authentication required"


class TestManagerLegacyFlyerAccess:
    @pytest.mark.asyncio
    async def test_list_flyers_includes_legacy_null_supermarket_id_when_name_matches(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_table.select.return_value.order.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "flyer-legacy",
                    "supermarket_id": None,
                    "supermarket_name": "Manager Market",
                    "status": "done",
                    "is_public": True,
                },
                {
                    "id": "flyer-other",
                    "supermarket_id": None,
                    "supermarket_name": "Other Market",
                    "status": "done",
                    "is_public": True,
                },
            ]
        )

        supermarkets_table = MagicMock()
        supermarkets_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"name": "Manager Market"}
        )

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                return flyers_table
            if table_name == "supermarkets":
                return supermarkets_table
            raise AssertionError(f"unexpected table {table_name}")

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get(
                "/flyers?admin=true",
                {_DEP_PROFILE: lambda: MANAGER_PROFILE},
            )

        assert resp.status_code == 200
        assert [row["id"] for row in resp.json()] == ["flyer-legacy"]

    @pytest.mark.asyncio
    async def test_get_flyer_allows_legacy_null_supermarket_id_when_name_matches(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={
                "id": "flyer-legacy",
                "supermarket_id": None,
                "supermarket_name": "Manager Market",
                "status": "done",
                "is_public": True,
            }
        )

        supermarkets_table = MagicMock()
        supermarkets_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"name": "Manager Market"}
        )

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                return flyers_table
            if table_name == "supermarkets":
                return supermarkets_table
            raise AssertionError(f"unexpected table {table_name}")

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get(
                "/flyers/flyer-legacy",
                {_DEP_PROFILE: lambda: MANAGER_PROFILE},
            )

        assert resp.status_code == 200
        assert resp.json()["id"] == "flyer-legacy"
