from __future__ import annotations

import io
import os
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub infrastructure modules
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_db_mod = types.ModuleType("core.database")
_db_mod.get_supabase = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.database"] = _db_mod

_config_mod = types.ModuleType("core.config")
_settings_obj = MagicMock()
_settings_obj.geocoding_provider = "disabled"
_config_mod.settings = _settings_obj  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod

sys.modules["services.geocoding"] = MagicMock()

# ---------------------------------------------------------------------------
# Stub core.auth — use MagicMock so FastAPI doesn't infer body params
# ---------------------------------------------------------------------------
_auth_mod = types.ModuleType("core.auth")
_auth_mod.get_current_user = MagicMock()  # type: ignore[attr-defined]
_auth_mod.require_admin = MagicMock()  # type: ignore[attr-defined]


async def _optional_user_id() -> str | None:
    return None


_auth_mod.get_optional_user_id = _optional_user_id  # type: ignore[attr-defined]
sys.modules["core.auth"] = _auth_mod

from fastapi import FastAPI, HTTPException
import httpx
import pytest

import api.routers.supermarkets as _sm_module
from api.routers.supermarkets import router

_DEP_REQUIRE_ADMIN = _sm_module.require_admin

test_app = FastAPI()
test_app.include_router(router, prefix="/supermarkets")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ADMIN_USER = {"id": "admin-1", "app_metadata": {"role": "admin"}}

MINIMAL_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"  # valid JPEG magic


def _admin_dep():
    return ADMIN_USER


def _deny_dep():
    raise HTTPException(status_code=403, detail="Admin access required")


def _logo_file(content: bytes = MINIMAL_JPEG, mime: str = "image/jpeg") -> tuple:
    return ("logo.jpg", io.BytesIO(content), mime)


async def _get(url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def _post_admin_form(url: str, data: dict, logo: tuple | None = None) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}
    files = {"logo": logo or _logo_file()}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, data=data, files=files)


async def _post_form_denied(url: str, data: dict) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _deny_dep}
    files = {"logo": _logo_file()}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, data=data, files=files)


async def _patch_logo(url: str, logo: tuple | None = None) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}
    files = {"logo": logo or _logo_file()}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(url, files=files)


async def _patch_logo_denied(url: str) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _deny_dep}
    files = {"logo": _logo_file()}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(url, files=files)


# ---------------------------------------------------------------------------
# GET tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_location_parameters_do_not_filter_guest_supermarkets():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-1", "distance_km": 1.2}]
    )
    sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-1", "name": "Lidl", "is_active": True}]
    )

    resp = await _get("/supermarkets?lat=45.464&lng=9.189&max_distance_km=10")

    assert resp.status_code == 428
    sb.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_guest_supermarkets_require_signed_location():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(
        data=[
            {"id": "sup-taurianova", "distance_km": 7.3},
            {"id": "sup-polistena", "distance_km": 1.1},
        ]
    )
    query = MagicMock()
    query.eq.return_value = query
    query.in_.return_value = query
    query.execute.return_value = MagicMock(
        data=[
            {"id": "sup-taurianova", "name": "Conad", "offers": [{"id": "offer-1"}]},
            {"id": "sup-polistena", "name": "Conad", "offers": [{"id": "offer-2"}]},
        ]
    )
    sb.table.return_value.select.return_value = query

    resp = await _get("/supermarkets?with_active_offers=true&lat=38.4&lng=16.1&max_distance_km=10")

    assert resp.status_code == 428


@pytest.mark.asyncio
async def test_with_active_offers_uses_authenticated_profile_radius():
    sb = MagicMock()
    with (
        patch("api.routers.supermarkets.get_supabase", return_value=sb),
        patch(
            "api.routers.supermarkets.request_location",
            return_value=(38.4, 16.1, 7.0),
        ) as request_location,
        patch(
            "api.routers.supermarkets._nearby_supermarkets",
            return_value=[{"id": "sup-polistena", "distance_km": 1.1}],
        ),
        patch(
            "api.routers.supermarkets._supermarkets_with_active_offers",
            return_value=[{"id": "sup-polistena", "name": "Conad"}],
        ),
    ):
        result = await _sm_module.list_supermarkets(
            with_active_offers=True,
            include_ids=[],
            user_id="user-1",
        )

    request_location.assert_called_once_with(sb, "user-1", None)
    assert result == [{"id": "sup-polistena", "name": "Conad", "distance_km": 1.1}]


@pytest.mark.asyncio
async def test_legacy_location_parameters_cannot_include_deep_link_supermarket():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-far", "name": "Conad", "is_active": True}]
    )

    resp = await _get(
        "/supermarkets?lat=45.464&lng=9.189&max_distance_km=10&include_ids=sm-far"
    )

    assert resp.status_code == 428


@pytest.mark.asyncio
async def test_admin_directory_ignores_authenticated_profile_distance():
    sb = MagicMock()
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.order.return_value = query
    query.execute.return_value = MagicMock(
        data=[
            {"id": "sm-near", "name": "Diper", "city": "Polistena"},
            {"id": "sm-far", "name": "Diper", "city": "Gioia Tauro"},
        ]
    )
    sb.table.return_value = query
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _get("/supermarkets/admin")

    assert resp.status_code == 200
    assert [row["id"] for row in resp.json()] == ["sm-near", "sm-far"]
    sb.rpc.assert_not_called()


# ---------------------------------------------------------------------------
# POST /supermarkets tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_supermarket_requires_admin():
    resp = await _post_form_denied("/supermarkets", {"name": "Nuovo Market"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_supermarket_success():
    new_row = {
        "id": "sm-new",
        "name": "Nuovo Market",
        "slug": "nuovo-market",
        "address": "Via Roma 1",
        "city": "Milano",
        "province": "Milano",
        "postal_code": "20100",
        "lat": None,
        "lng": None,
        "is_active": True,
        "logo_url": None,
    }
    updated_row = {**new_row, "logo_url": "https://example.com/logos/sm-new.jpg"}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[new_row])
    sb.storage.from_.return_value.upload.return_value = None
    sb.storage.from_.return_value.get_public_url.return_value = "https://example.com/logos/sm-new.jpg"
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_row])

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _post_admin_form(
            "/supermarkets",
            {
                "name": "Nuovo Market",
                "address": "Via Roma 1",
                "city": "Milano",
                "province": "Milano",
                "postal_code": "20100",
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Nuovo Market"
    assert data["logo_url"] == "https://example.com/logos/sm-new.jpg"


@pytest.mark.asyncio
async def test_create_supermarket_skips_geocode_when_coords_provided():
    new_row = {"id": "sm-2", "name": "Test", "slug": "test", "lat": 45.5, "lng": 9.2, "is_active": True, "logo_url": None}
    updated_row = {**new_row, "logo_url": "https://example.com/logos/sm-2.jpg"}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[new_row])
    sb.storage.from_.return_value.upload.return_value = None
    sb.storage.from_.return_value.get_public_url.return_value = "https://example.com/logos/sm-2.jpg"
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_row])

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        with patch("api.routers.supermarkets.geocode_address") as mock_geocode:
            resp = await _post_admin_form(
                "/supermarkets",
                {"name": "Test", "address": "Via Po 5", "city": "Torino",
                 "province": "Torino", "postal_code": "10100", "lat": 45.5, "lng": 9.2},
            )

    assert resp.status_code == 201
    mock_geocode.assert_not_called()


@pytest.mark.asyncio
async def test_create_supermarket_geocodes_when_no_coords():
    new_row = {"id": "sm-3", "name": "Geocoded", "slug": "geocoded", "lat": 44.4, "lng": 8.9, "is_active": True, "logo_url": None}
    updated_row = {**new_row, "logo_url": "https://example.com/logos/sm-3.jpg"}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[new_row])
    sb.storage.from_.return_value.upload.return_value = None
    sb.storage.from_.return_value.get_public_url.return_value = "https://example.com/logos/sm-3.jpg"
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_row])

    _settings_obj.geocoding_provider = "nominatim"
    try:
        with patch("api.routers.supermarkets.get_supabase", return_value=sb):
            with patch("api.routers.supermarkets.geocode_address", return_value=(44.4, 8.9)) as mock_geocode:
                resp = await _post_admin_form(
                    "/supermarkets",
                    {"name": "Geocoded", "address": "Via Garibaldi 3",
                     "city": "Genova", "province": "Genova", "postal_code": "16100"},
                )
    finally:
        _settings_obj.geocoding_provider = "disabled"

    assert resp.status_code == 201
    mock_geocode.assert_called_once()


# ---------------------------------------------------------------------------
# Logo validation tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_supermarket_logo_wrong_type_rejected():
    resp = await _post_admin_form(
        "/supermarkets",
        {"name": "Test"},
        logo=("logo.pdf", io.BytesIO(b"%PDF"), "application/pdf"),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_supermarket_logo_too_large_rejected():
    big_content = b"\xff\xd8\xff" + b"x" * (2 * 1024 * 1024 + 1)
    resp = await _post_admin_form(
        "/supermarkets",
        {"name": "Test"},
        logo=("logo.jpg", io.BytesIO(big_content), "image/jpeg"),
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_create_supermarket_logo_upload_failure_rolls_back():
    new_row = {"id": "sm-fail", "name": "Rollback", "slug": "rollback", "is_active": True, "logo_url": None}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[new_row])
    sb.storage.from_.return_value.upload.side_effect = Exception("Storage down")

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _post_admin_form("/supermarkets", {"name": "Rollback"})

    assert resp.status_code == 500
    sb.table.return_value.delete.return_value.eq.return_value.execute.assert_called_once()


# ---------------------------------------------------------------------------
# PATCH /supermarkets/{id}/logo tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_logo_requires_admin():
    resp = await _patch_logo_denied("/supermarkets/sm-1/logo")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_logo_not_found():
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=None)

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _patch_logo("/supermarkets/nonexistent/logo")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_logo_wrong_type_rejected():
    resp = await _patch_logo(
        "/supermarkets/sm-1/logo",
        logo=("logo.gif", io.BytesIO(b"GIF89a"), "image/gif"),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_logo_too_large_rejected():
    big_content = b"\xff\xd8\xff" + b"x" * (2 * 1024 * 1024 + 1)
    resp = await _patch_logo(
        "/supermarkets/sm-1/logo",
        logo=("logo.jpg", io.BytesIO(big_content), "image/jpeg"),
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_update_logo_success():
    existing = {"id": "sm-1", "logo_url": None}
    updated_row = {"id": "sm-1", "name": "Test", "logo_url": "https://example.com/logos/sm-1.jpg"}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=existing)
    sb.storage.from_.return_value.upload.return_value = None
    sb.storage.from_.return_value.get_public_url.return_value = "https://example.com/logos/sm-1.jpg"
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_row])

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _patch_logo("/supermarkets/sm-1/logo")

    assert resp.status_code == 200
    assert resp.json()["logo_url"] == "https://example.com/logos/sm-1.jpg"


@pytest.mark.asyncio
async def test_update_logo_deletes_old_file_when_present():
    old_url = "https://proj.supabase.co/storage/v1/object/public/logos/sm-1.jpg"
    existing = {"id": "sm-1", "logo_url": old_url}
    updated_row = {"id": "sm-1", "logo_url": "https://proj.supabase.co/storage/v1/object/public/logos/sm-1.png"}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=existing)
    sb.storage.from_.return_value.upload.return_value = None
    sb.storage.from_.return_value.get_public_url.return_value = updated_row["logo_url"]
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_row])

    _settings_obj.supabase_url = "https://proj.supabase.co"
    try:
        with patch("api.routers.supermarkets.get_supabase", return_value=sb):
            resp = await _patch_logo(
                "/supermarkets/sm-1/logo",
                logo=("logo.png", io.BytesIO(b"\x89PNG"), "image/png"),
            )
    finally:
        _settings_obj.supabase_url = MagicMock()

    assert resp.status_code == 200
    sb.storage.from_.return_value.remove.assert_called_once_with(["sm-1.jpg"])


# ---------------------------------------------------------------------------
# PATCH /supermarkets/{id} (info update) helpers
# ---------------------------------------------------------------------------

async def _patch_info(url: str, data: dict) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(url, json=data)


async def _patch_info_denied(url: str, data: dict) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _deny_dep}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(url, json=data)


# ---------------------------------------------------------------------------
# PATCH /supermarkets/{id} (info update) tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_supermarket_requires_admin():
    resp = await _patch_info_denied("/supermarkets/sm-1", {"name": "Nuovo Nome"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_supermarket_not_found():
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=None)
    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _patch_info("/supermarkets/nonexistent", {"name": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_supermarket_success():
    existing = {"id": "sm-1"}
    updated_row = {"id": "sm-1", "name": "Nuovo Nome", "address": "Via Nuova 1", "city": "Roma", "province": "RM", "postal_code": "00100", "logo_url": None}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=existing)
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_row])
    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _patch_info("/supermarkets/sm-1", {"name": "Nuovo Nome", "address": "Via Nuova 1", "city": "Roma", "province": "RM", "postal_code": "00100"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Nuovo Nome"


@pytest.mark.asyncio
async def test_update_supermarket_empty_body_returns_current():
    current_row = {"id": "sm-1", "name": "Esistente", "logo_url": None}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=current_row)
    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _patch_info("/supermarkets/sm-1", {})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Esistente"


@pytest.mark.asyncio
async def test_update_supermarket_geocodes_when_address_changes():
    existing = {"id": "sm-1", "address": "Via Vecchia 1", "city": "Roma", "province": "RM", "postal_code": "00100"}
    updated_row = {"id": "sm-1", "name": "Test", "lat": 41.9, "lng": 12.5}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=existing)
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_row])
    _settings_obj.geocoding_provider = "nominatim"
    try:
        with patch("api.routers.supermarkets.get_supabase", return_value=sb):
            with patch("api.routers.supermarkets.geocode_address", return_value=(41.9, 12.5)) as mock_geo:
                resp = await _patch_info("/supermarkets/sm-1", {"address": "Via Roma 1", "city": "Roma"})
    finally:
        _settings_obj.geocoding_provider = "disabled"
    assert resp.status_code == 200
    mock_geo.assert_called_once()
    call_args = mock_geo.call_args[0][0]
    assert "Via Roma 1" in call_args
    assert "Roma" in call_args


@pytest.mark.asyncio
async def test_update_supermarket_no_geocode_when_lat_explicitly_provided():
    existing = {"id": "sm-1", "address": "Via Vecchia 1", "city": "Milano", "province": "MI", "postal_code": "20100"}
    updated_row = {**existing, "address": "Via Nuova 1", "lat": None, "lng": None}
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=existing)
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_row])
    _settings_obj.geocoding_provider = "nominatim"
    try:
        with patch("api.routers.supermarkets.get_supabase", return_value=sb):
            with patch("api.routers.supermarkets.geocode_address") as mock_geo:
                resp = await _patch_info("/supermarkets/sm-1", {"address": "Via Nuova 1", "lat": None})
    finally:
        _settings_obj.geocoding_provider = "disabled"
    assert resp.status_code == 200
    mock_geo.assert_not_called()
