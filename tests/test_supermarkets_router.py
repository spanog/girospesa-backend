from __future__ import annotations

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


def _admin_dep():
    return ADMIN_USER


def _deny_dep():
    raise HTTPException(status_code=403, detail="Admin access required")


async def _get(url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def _post_admin(url: str, json_body: dict) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, json=json_body)


async def _post_denied(url: str, json_body: dict) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _deny_dep}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, json=json_body)


# ---------------------------------------------------------------------------
# GET tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lat_lng_returns_postgis_nearby_supermarkets():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-1", "distance_km": 1.2}]
    )
    sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-1", "name": "Lidl", "is_active": True}]
    )

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _get("/supermarkets?lat=45.464&lng=9.189&max_distance_km=10")

    assert resp.status_code == 200
    assert resp.json() == [
        {"id": "sm-1", "name": "Lidl", "is_active": True, "distance_km": 1.2}
    ]
    sb.rpc.assert_called_once_with(
        "nearby_supermarkets",
        {
            "user_lat": 45.464,
            "user_lng": 9.189,
            "radius_m": 10000.0,
        },
    )


# ---------------------------------------------------------------------------
# POST /supermarkets tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_supermarket_requires_admin():
    resp = await _post_denied("/supermarkets", {"name": "Nuovo Market"})
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
    }
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[new_row])

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _post_admin(
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
    assert data["slug"] == "nuovo-market"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_supermarket_skips_geocode_when_coords_provided():
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-2", "name": "Test", "slug": "test", "lat": 45.5, "lng": 9.2, "is_active": True}]
    )

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        with patch("api.routers.supermarkets.geocode_address") as mock_geocode:
            resp = await _post_admin(
                "/supermarkets",
                {"name": "Test", "address": "Via Po 5", "city": "Torino",
                 "province": "Torino", "postal_code": "10100", "lat": 45.5, "lng": 9.2},
            )

    assert resp.status_code == 201
    mock_geocode.assert_not_called()


@pytest.mark.asyncio
async def test_create_supermarket_geocodes_when_no_coords():
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-3", "name": "Geocoded", "slug": "geocoded", "lat": 44.4, "lng": 8.9, "is_active": True}]
    )

    _settings_obj.geocoding_provider = "nominatim"
    try:
        with patch("api.routers.supermarkets.get_supabase", return_value=sb):
            with patch("api.routers.supermarkets.geocode_address", return_value=(44.4, 8.9)) as mock_geocode:
                resp = await _post_admin(
                    "/supermarkets",
                    {"name": "Geocoded", "address": "Via Garibaldi 3",
                     "city": "Genova", "province": "Genova", "postal_code": "16100"},
                )
    finally:
        _settings_obj.geocoding_provider = "disabled"

    assert resp.status_code == 201
    mock_geocode.assert_called_once()
    inserted_row = sb.table.return_value.insert.call_args[0][0]
    assert inserted_row["lat"] == 44.4
    assert inserted_row["lng"] == 8.9
