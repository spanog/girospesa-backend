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
    managed_ids = _managed_supermarket_ids_real(profile)
    if flyer.get("supermarket_id") not in managed_ids:
        raise HTTPException(
            status_code=403,
            detail="Access denied: flyer belongs to a different supermarket",
        )


def _managed_supermarket_ids_real(profile: dict) -> list[str]:
    ids = profile.get("managed_supermarket_ids")
    if isinstance(ids, list):
        return ids
    managed = profile.get("managed_supermarket_id")
    return [managed] if managed else []


_auth_mod.assert_flyer_access = _assert_flyer_access_real  # type: ignore[attr-defined]
_auth_mod.managed_supermarket_ids = _managed_supermarket_ids_real  # type: ignore[attr-defined]


async def _optional_user_id() -> str | None:
    return None


_auth_mod.get_optional_user_id = _optional_user_id  # type: ignore[attr-defined]


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
MANAGER_PROFILE = {
    "id": "mgr-123",
    "role": "supermarket_manager",
    "managed_supermarket_id": "sup-1",
    "managed_supermarket_ids": ["sup-1"],
}

_SMALL_PDF = b"%PDF-1.4 fake pdf content"


def _mock_supabase_for_upload(insert_return: Optional[dict] = None) -> MagicMock:
    """Return mock Supabase that simulates Storage download + table insert."""
    sb = MagicMock()
    sb.storage.from_.return_value.download.return_value = _SMALL_PDF
    sb.storage.from_.return_value.remove.return_value = MagicMock()
    sb.storage.from_.return_value.get_public_url.return_value = "https://storage.example.com/flyers/test.pdf"
    row_data = insert_return or {
        "id": "flyer-uuid",
        "user_id": "admin-456",
        "status": "pending",
        "is_public": False,
    }
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[row_data])
    return sb


async def _post_upload(dep_overrides: dict, data: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    user_id = dep_overrides[_DEP_GET_USER_ID]()
    body = {
        "storage_path": f"{user_id}/test.pdf",
        "file_name": "test.pdf",
        "content_type": "application/pdf",
        "supermarket_ids": ["sup-1"],
    }
    body.update(data or {})
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/flyers/upload/complete", json=body)


async def _get(url: str, dep_overrides: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides or {}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def _patch(url: str, dep_overrides: dict, json: dict) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(url, json=json)


async def _put(url: str, dep_overrides: dict, json: dict) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.put(url, json=json)


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
            )

        assert resp.status_code == 201
        insert_call_kwargs = sb.table.return_value.insert.call_args_list[0][0][0]
        assert insert_call_kwargs["is_public"] is False

    @pytest.mark.asyncio
    async def test_upload_ignores_is_public_form_field(self):
        """Upload endpoint ignores any submitted is_public field and keeps flyer private."""
        sb = _mock_supabase_for_upload({"id": "f2", "is_public": False, "status": "pending", "user_id": "admin-456"})

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
                data={"is_public": "true", "supermarket_ids": ["sup-1"]},
            )

        assert resp.status_code == 201
        insert_call_kwargs = sb.table.return_value.insert.call_args_list[0][0][0]
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
                data={"storage_path": "mgr-123/test.pdf", "supermarket_ids": []},
            )

        assert resp.status_code == 201
        insert_call_kwargs = sb.table.return_value.insert.call_args_list[0][0][0]
        assert insert_call_kwargs["supermarket_id"] == "sup-1"

    @pytest.mark.asyncio
    async def test_manager_wrong_supermarket_id_403(self):
        """Manager providing a different supermarket_id receives 403."""
        sb = _mock_supabase_for_upload()

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "mgr-123", _DEP_PROFILE: lambda: MANAGER_PROFILE},
                data={"storage_path": "mgr-123/test.pdf", "supermarket_ids": ["sup-other"]},
            )

        assert resp.status_code == 403


class TestUploadFlyerValidation:
    @pytest.mark.asyncio
    async def test_unsupported_content_type_rejected(self):
        """Files with unsupported MIME types return 422."""
        resp = await _post_upload(
            {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
            data={"content_type": "text/plain", "file_name": "test.txt"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_oversized_file_rejected(self):
        """Files exceeding 50 MB return 413."""
        sb = _mock_supabase_for_upload()
        sb.storage.from_.return_value.download.return_value = b"x" * (50 * 1024 * 1024 + 1)
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_upload({_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE})
        assert resp.status_code == 413


class TestUploadFlyerDuplicate:
    @pytest.mark.asyncio
    async def test_duplicate_hash_and_supermarket_returns_409(self):
        """Uploading the same file+supermarket twice returns 409 Conflict."""
        sb = MagicMock()
        sb.storage.from_.return_value.download.return_value = _SMALL_PDF
        sb.storage.from_.return_value.remove.return_value = MagicMock()
        sb.storage.from_.return_value.get_public_url.return_value = "https://storage.example.com/flyers/dup.pdf"
        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch("api.routers.flyers._duplicate_target_conflicts", return_value={"sup-1"}),
        ):
            resp = await _post_upload(
                {_DEP_GET_USER_ID: lambda: "admin-456", _DEP_PROFILE: lambda: ADMIN_PROFILE},
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
            )

        assert resp.status_code == 201
        assert sb.table.return_value.insert.call_count >= 1


class TestPublicFlyersVisibility:
    @pytest.mark.asyncio
    async def test_public_list_keeps_all_targets_when_location_is_provided(self):
        sb = MagicMock()
        flyers_table = MagicMock()
        query = flyers_table.select.return_value
        query.eq.return_value = query
        query.order.return_value = query
        first_page = MagicMock()
        first_page.execute.return_value = MagicMock(
            count=2,
            data=[
                {
                    "id": "flyer-taurianova",
                    "source_flyer_id": "source-conad",
                    "supermarket_id": "taurianova",
                }
            ],
        )
        second_page = MagicMock()
        second_page.execute.return_value = MagicMock(
            count=2,
            data=[
                {
                    "id": "flyer-polistena",
                    "source_flyer_id": "source-conad",
                    "supermarket_id": "polistena",
                }
            ],
        )
        empty_page = MagicMock()
        empty_page.execute.return_value = MagicMock(data=[])
        query.range.side_effect = [first_page, second_page, empty_page]
        sb.table.return_value = flyers_table

        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch(
                "api.routers.flyers.nearby_supermarket_distances",
                return_value={"taurianova": 7.3, "polistena": 1.1},
            ),
            patch(
                "api.routers.flyers._confirmed_count_by_flyer",
                return_value={"flyer-polistena": 306, "flyer-taurianova": 306},
            ),
        ):
            resp = await _get("/flyers/public?lat=38.6&lng=16.0")

        assert resp.status_code == 200
        assert resp.json() == [
            {
                "id": "flyer-taurianova",
                "source_flyer_id": "source-conad",
                "supermarket_id": "taurianova",
                "confirmed_count": 306,
            },
            {
                "id": "flyer-polistena",
                "source_flyer_id": "source-conad",
                "supermarket_id": "polistena",
                "confirmed_count": 306,
            }
        ]

    @pytest.mark.asyncio
    async def test_public_list_excludes_unconfirmed_flyers(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_result = MagicMock()
        flyers_result.data = [
            {"id": "flyer-hidden", "supermarket_id": "sup-hidden", "status": "done", "is_public": True, "flyer_kind": "published_target"},
            {"id": "flyer-visible", "supermarket_id": "sup-visible", "status": "done", "is_public": True, "flyer_kind": "published_target"},
        ]
        query = flyers_table.select.return_value
        query.eq.return_value = query
        query.order.return_value = query
        empty_page = MagicMock()
        empty_page.execute.return_value = MagicMock(data=[])
        query.range.side_effect = [query, empty_page]
        query.execute.return_value = flyers_result

        offers_table = MagicMock()
        confirmed_result = MagicMock()
        confirmed_result.data = [{"flyer_id": "flyer-visible"}]
        offers_table.select.return_value.in_.return_value.eq.return_value.execute.return_value = confirmed_result

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                return flyers_table
            return offers_table

        sb.table.side_effect = _dispatch

        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch(
                "api.routers.flyers.nearby_supermarket_distances",
                return_value={"sup-hidden": 1.0, "sup-visible": 1.5},
            ),
        ):
            resp = await _get("/flyers/public?lat=38.6&lng=16.0")

        assert resp.status_code == 200
        assert resp.json() == [
            {
                "id": "flyer-visible",
                "supermarket_id": "sup-visible",
                "status": "done",
                "is_public": True,
                "flyer_kind": "published_target",
                "confirmed_count": 1,
            }
        ]

    @pytest.mark.asyncio
    async def test_public_list_excludes_future_public_flyers(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_result = MagicMock()
        flyers_result.data = [
            {
                "id": "flyer-future",
                "supermarket_id": "sup-future",
                "status": "done",
                "is_public": True,
                "flyer_kind": "published_target",
                "valid_from": "2099-01-01",
            },
            {"id": "flyer-visible", "supermarket_id": "sup-visible", "status": "done", "is_public": True, "flyer_kind": "published_target"},
        ]
        query = flyers_table.select.return_value
        query.eq.return_value = query
        query.order.return_value = query
        empty_page = MagicMock()
        empty_page.execute.return_value = MagicMock(data=[])
        query.range.side_effect = [query, empty_page]
        query.execute.return_value = flyers_result

        offers_table = MagicMock()
        confirmed_result = MagicMock()
        confirmed_result.data = [
            {"flyer_id": "flyer-future"},
            {"flyer_id": "flyer-visible"},
        ]
        offers_table.select.return_value.in_.return_value.eq.return_value.execute.return_value = confirmed_result

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                return flyers_table
            return offers_table

        sb.table.side_effect = _dispatch

        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch(
                "api.routers.flyers.nearby_supermarket_distances",
                return_value={"sup-future": 1.0, "sup-visible": 1.5},
            ),
        ):
            resp = await _get("/flyers/public?lat=38.6&lng=16.0")

        assert resp.status_code == 200
        assert resp.json() == [
            {
                "id": "flyer-visible",
                "supermarket_id": "sup-visible",
                "status": "done",
                "is_public": True,
                "flyer_kind": "published_target",
                "confirmed_count": 1,
            }
        ]

    @pytest.mark.asyncio
    async def test_public_list_excludes_flyers_outside_the_active_radius(self):
        sb = MagicMock()
        flyers_table = MagicMock()
        query = flyers_table.select.return_value
        query.eq.return_value = query
        query.order.return_value = query
        page = MagicMock()
        page.execute.return_value = MagicMock(
            data=[
                {"id": "flyer-near", "supermarket_id": "sup-near"},
                {"id": "flyer-far", "supermarket_id": "sup-far"},
            ]
        )
        empty_page = MagicMock()
        empty_page.execute.return_value = MagicMock(data=[])
        query.range.side_effect = [page, empty_page]
        sb.table.return_value = flyers_table

        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch(
                "api.routers.flyers.nearby_supermarket_distances",
                return_value={"sup-near": 1.2},
            ),
            patch(
                "api.routers.flyers._confirmed_count_by_flyer",
                return_value={"flyer-near": 2},
            ),
        ):
            resp = await _get("/flyers/public?lat=38.6&lng=16.0")

        assert resp.status_code == 200
        assert resp.json() == [
            {"id": "flyer-near", "supermarket_id": "sup-near", "confirmed_count": 2}
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


class TestManagerFlyerTargetsAccess:
    @pytest.mark.asyncio
    async def test_list_flyers_includes_source_flyer_when_manager_owns_one_target(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_table.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "flyer-source",
                    "supermarket_id": "sup-1",
                    "supermarket_name": "Manager Market",
                    "status": "done",
                    "is_public": False,
                    "flyer_kind": "source",
                },
                {
                    "id": "flyer-other",
                    "supermarket_id": "sup-other",
                    "supermarket_name": "Other Market",
                    "status": "done",
                    "is_public": False,
                    "flyer_kind": "source",
                },
            ]
        )

        flyer_targets_table = MagicMock()
        flyer_targets_table.select.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[{"supermarket_id": "sup-1", "supermarkets": {"name": "Manager Market"}}]),
            MagicMock(data=[{"supermarket_id": "sup-other", "supermarkets": {"name": "Other Market"}}]),
            MagicMock(data=[{"supermarket_id": "sup-other", "supermarkets": {"name": "Other Market"}}]),
        ]

        offers_table = MagicMock()
        offers_table.select.return_value.in_.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[]),
        ]

        published_targets_query = MagicMock()
        published_targets_query.eq.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[]
        )

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                class _FlyersDispatch:
                    def select(self, *args, **kwargs):
                        if args == ("*",):
                            return flyers_table.select(*args, **kwargs)
                        return published_targets_query

                return _FlyersDispatch()
            if table_name == "flyer_targets":
                return flyer_targets_table
            if table_name == "offers":
                return offers_table
            raise AssertionError(f"unexpected table {table_name}")

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get(
                "/flyers",
                {_DEP_PROFILE: lambda: MANAGER_PROFILE},
            )

        assert resp.status_code == 200
        assert [row["id"] for row in resp.json()] == ["flyer-source"]

    @pytest.mark.asyncio
    async def test_list_flyers_ignores_legacy_admin_query_flag(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_table.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "flyer-source",
                    "supermarket_id": "sup-1",
                    "supermarket_name": "Manager Market",
                    "status": "done",
                    "is_public": False,
                    "flyer_kind": "source",
                },
                {
                    "id": "flyer-other",
                    "supermarket_id": "sup-other",
                    "supermarket_name": "Other Market",
                    "status": "done",
                    "is_public": False,
                    "flyer_kind": "source",
                },
            ]
        )

        flyer_targets_table = MagicMock()
        flyer_targets_table.select.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[{"supermarket_id": "sup-1", "supermarkets": {"name": "Manager Market"}}]),
            MagicMock(data=[{"supermarket_id": "sup-other", "supermarkets": {"name": "Other Market"}}]),
            MagicMock(data=[{"supermarket_id": "sup-other", "supermarkets": {"name": "Other Market"}}]),
            MagicMock(data=[{"supermarket_id": "sup-1", "supermarkets": {"name": "Manager Market"}}]),
            MagicMock(data=[{"supermarket_id": "sup-other", "supermarkets": {"name": "Other Market"}}]),
            MagicMock(data=[{"supermarket_id": "sup-other", "supermarkets": {"name": "Other Market"}}]),
        ]

        offers_table = MagicMock()
        offers_table.select.return_value.in_.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[]),
            MagicMock(data=[]),
            MagicMock(data=[]),
        ]

        published_targets_query = MagicMock()
        published_targets_query.eq.return_value.in_.return_value.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[]),
        ]

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                class _FlyersDispatch:
                    def select(self, *args, **kwargs):
                        if args == ("*",):
                            return flyers_table.select(*args, **kwargs)
                        return published_targets_query

                return _FlyersDispatch()
            if table_name == "flyer_targets":
                return flyer_targets_table
            if table_name == "offers":
                return offers_table
            raise AssertionError(f"unexpected table {table_name}")

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            response_without_flag = await _get(
                "/flyers",
                {_DEP_PROFILE: lambda: MANAGER_PROFILE},
            )
            response_with_flag = await _get(
                "/flyers?admin=true",
                {_DEP_PROFILE: lambda: MANAGER_PROFILE},
            )

        assert response_without_flag.status_code == 200
        assert response_with_flag.status_code == 200
        assert response_with_flag.json() == response_without_flag.json()

    @pytest.mark.asyncio
    async def test_list_flyers_includes_confirmation_counts_for_source_flyer(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_table.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "flyer-source",
                    "supermarket_id": "sup-1",
                    "supermarket_name": "Manager Market",
                    "status": "done",
                    "is_public": False,
                    "flyer_kind": "source",
                }
            ]
        )

        flyer_targets_table = MagicMock()
        flyer_targets_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"supermarket_id": "sup-1", "supermarkets": {"name": "Manager Market"}}]
        )

        offers_table = MagicMock()
        offers_table.select.return_value.in_.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[{"flyer_id": "flyer-source"}, {"flyer_id": "flyer-source"}]),
        ]

        published_targets_query = MagicMock()
        published_targets_query.eq.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[{"source_flyer_id": "flyer-source"}]
        )

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                class _FlyersDispatch:
                    def select(self, *args, **kwargs):
                        if args == ("*",):
                            return flyers_table.select(*args, **kwargs)
                        return published_targets_query

                return _FlyersDispatch()
            if table_name == "flyer_targets":
                return flyer_targets_table
            if table_name == "offers":
                return offers_table
            raise AssertionError(f"unexpected table {table_name}")

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get(
                "/flyers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
            )

        assert resp.status_code == 200
        assert resp.json()[0]["draft_count"] == 0
        assert resp.json()[0]["confirmed_count"] == 2
        assert resp.json()[0]["published_target_count"] == 1

    @pytest.mark.asyncio
    async def test_get_flyer_allows_source_flyer_when_manager_owns_one_target(self):
        sb = MagicMock()

        flyers_table = MagicMock()
        flyers_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={
                "id": "flyer-source",
                "supermarket_id": "sup-1",
                "supermarket_name": "Manager Market",
                "status": "done",
                "is_public": False,
                "flyer_kind": "source",
            }
        )

        flyer_targets_table = MagicMock()
        flyer_targets_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"supermarket_id": "sup-1", "supermarkets": {"name": "Manager Market"}}]
        )

        offers_table = MagicMock()
        offers_table.select.return_value.in_.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[]),
            MagicMock(data=[]),
        ]

        published_targets_query = MagicMock()
        published_targets_query.eq.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[]
        )

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                class _FlyersDispatch:
                    def select(self, *args, **kwargs):
                        if args == ("*",):
                            return flyers_table.select(*args, **kwargs)
                        return published_targets_query

                return _FlyersDispatch()
            if table_name == "flyer_targets":
                return flyer_targets_table
            if table_name == "offers":
                return offers_table
            raise AssertionError(f"unexpected table {table_name}")

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get(
                "/flyers/flyer-source",
                {_DEP_PROFILE: lambda: MANAGER_PROFILE},
            )

        assert resp.status_code == 200
        assert resp.json()["id"] == "flyer-source"


class TestUpdateFlyerValidity:
    @pytest.mark.asyncio
    async def test_patch_updates_source_and_published_offer_dates(self):
        sb = MagicMock()

        source_flyer = {
            "id": "flyer-source",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "status": "done",
            "is_public": False,
            "flyer_kind": "source",
            "valid_from": "2026-04-01",
            "valid_to": "2026-04-07",
        }
        updated_flyer = {
            **source_flyer,
            "valid_from": "2026-05-01",
            "valid_to": "2026-05-10",
        }

        flyers_table = MagicMock()

        select_call = 0

        def flyers_select_side_effect(*_args, **_kwargs):
            nonlocal select_call
            select_call += 1
            chain = MagicMock()
            if select_call == 1:
                chain.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
                    data=source_flyer
                )
            elif select_call == 2:
                chain.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                    data=[{"id": "flyer-published-1"}]
                )
            else:
                chain.eq.return_value.single.return_value.execute.return_value = MagicMock(
                    data=updated_flyer
                )
            return chain

        flyers_table.select.side_effect = flyers_select_side_effect
        flyers_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        offers_table = MagicMock()
        offers_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        flyer_targets_table = MagicMock()
        flyer_targets_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "flyers":
                return flyers_table
            if table_name == "offers":
                return offers_table
            if table_name == "flyer_targets":
                return flyer_targets_table
            raise AssertionError(f"unexpected table {table_name}")

        sb.table.side_effect = _dispatch

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _patch(
                "/flyers/flyer-source",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                {"valid_from": "2026-05-01", "valid_to": "2026-05-10"},
            )

        assert resp.status_code == 200
        assert resp.json()["valid_from"] == "2026-05-01"
        assert resp.json()["valid_to"] == "2026-05-10"
        assert flyers_table.update.call_count == 2
        assert offers_table.update.call_count == 2
        assert all(
            call.args[0] == {"valid_from": "2026-05-01", "valid_to": "2026-05-10"}
            for call in flyers_table.update.call_args_list + offers_table.update.call_args_list
        )


class TestUpdateFlyerTargets:
    def test_duplicate_check_ignores_same_source_published_targets(self):
        sb = MagicMock()
        flyers = [
            {
                "flyer_kind": "published_target",
                "supermarket_id": "sup-1",
                "source_flyer_id": "flyer-source",
            },
            {
                "flyer_kind": "published_target",
                "supermarket_id": "sup-2",
                "source_flyer_id": "flyer-other",
            },
        ]
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=flyers
        )

        conflicts = _flyers_module._duplicate_target_conflicts(
            sb,
            file_hash="hash-1",
            supermarket_ids=["sup-1", "sup-2"],
            exclude_source_flyer_id="flyer-source",
        )

        assert conflicts == {"sup-2"}

    @pytest.mark.asyncio
    async def test_put_targets_syncs_published_offers_after_confirmation(self):
        sb = MagicMock()
        source_flyer = {
            "id": "flyer-source",
            "user_id": "admin-456",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "status": "done",
            "is_public": False,
            "flyer_kind": "source",
            "file_hash": "hash-1",
        }
        updated_flyer = {**source_flyer, "supermarket_id": "sup-2"}
        flyers_table = MagicMock()
        select_chain = flyers_table.select.return_value.eq.return_value
        select_chain.maybe_single.return_value.execute.return_value = MagicMock(
            data=source_flyer
        )
        select_chain.single.return_value.execute.return_value = MagicMock(
            data=updated_flyer
        )
        flyers_table.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )
        sb.table.return_value = flyers_table
        targets = [
            {"supermarket_id": "sup-1", "supermarket_name": "Coop"},
            {"supermarket_id": "sup-2", "supermarket_name": "Esselunga"},
        ]

        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch("api.routers.flyers._duplicate_target_conflicts", return_value=set()),
            patch("api.routers.flyers._replace_flyer_targets") as replace_mock,
            patch(
                "api.routers.flyers._supermarket_name_map",
                return_value={"sup-1": "Coop", "sup-2": "Esselunga"},
            ),
            patch("api.routers.flyers._flyer_targets", return_value=targets),
            patch(
                "api.routers.flyers._source_master_offers",
                return_value=[{"id": "offer-1"}],
            ),
            patch(
                "api.routers.flyers._sync_published_targets_for_source_flyer"
            ) as sync_mock,
        ):
            resp = await _put(
                "/flyers/flyer-source/targets",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                {"supermarket_ids": ["sup-1", "sup-2"]},
            )

        assert resp.status_code == 200
        replace_mock.assert_called_once_with(
            sb,
            flyer_id="flyer-source",
            supermarket_ids=["sup-1", "sup-2"],
        )
        sync_mock.assert_called_once_with(
            sb,
            source_flyer=updated_flyer,
            targets=targets,
            notify_new=True,
            source_offers=[{"id": "offer-1"}],
        )

    def test_sync_published_targets_adds_and_removes_public_materialization(self):
        sb = MagicMock()
        offers_table = MagicMock()
        flyers_table = MagicMock()
        flyers_table.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "flyer-pub-2"}]
        )

        def _dispatch(table_name: str) -> MagicMock:
            if table_name == "offers":
                return offers_table
            if table_name == "flyers":
                return flyers_table
            raise AssertionError(f"unexpected table {table_name}")

        sb.table.side_effect = _dispatch
        source_flyer = {
            "id": "flyer-source",
            "user_id": "admin-456",
            "file_url": "https://example.com/flyer.pdf",
            "file_type": "pdf",
            "file_name": "volantino.pdf",
            "valid_from": "2026-05-01",
            "valid_to": "2026-05-10",
            "pages_count": 2,
            "extraction_metadata": None,
            "file_hash": "hash-1",
        }
        targets = [
            {"supermarket_id": "sup-1", "supermarket_name": "Coop"},
            {"supermarket_id": "sup-2", "supermarket_name": "Esselunga"},
        ]
        target_flyers_after = {
            "sup-1": {"flyer_id": "flyer-pub-1", "supermarket_name": "Coop"},
            "sup-2": {"flyer_id": "flyer-pub-2", "supermarket_name": "Esselunga"},
        }

        with (
            patch(
                "api.routers.flyers._published_target_flyers",
                side_effect=[
                    {
                        "sup-1": {"flyer_id": "flyer-pub-1", "supermarket_name": "Coop"},
                        "sup-old": {
                            "flyer_id": "flyer-pub-old",
                            "supermarket_name": "Old",
                        },
                    },
                    target_flyers_after,
                ],
            ),
            patch(
                "api.routers.flyers._sync_published_clones_for_source_offers",
                return_value={"flyer-pub-1": 1, "flyer-pub-2": 1},
            ) as clone_sync,
            patch("api.routers.flyers.enqueue_flyer_published") as flyer_job_mock,
        ):
            counts = _flyers_module._sync_published_targets_for_source_flyer(
                sb,
                source_flyer=source_flyer,
                targets=targets,
                notify_new=True,
                source_offers=[{"id": "offer-1"}],
            )

        offers_table.delete.return_value.in_.assert_called_once_with(
            "flyer_id",
            ["flyer-pub-old"],
        )
        flyers_table.delete.return_value.in_.assert_called_once_with(
            "id",
            ["flyer-pub-old"],
        )
        assert flyers_table.insert.call_args.args[0]["supermarket_id"] == "sup-2"
        clone_sync.assert_called_once_with(
            sb,
            source_offers=[{"id": "offer-1"}],
            target_flyers=target_flyers_after,
        )
        flyer_job_mock.assert_called_once()
        assert counts == {"flyer-pub-1": 1, "flyer-pub-2": 1}
