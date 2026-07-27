"""Unit tests for manual offer creation in api/routers/offers.py."""

from __future__ import annotations

import sys
import os
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub infrastructure modules
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
sys.modules.pop("core.auth", None)

from fastapi import FastAPI
import httpx
import pytest

import api.routers.offers as _offers_module
from api.routers.offers import router

_DEP_PROFILE = _offers_module.require_admin_or_manager

test_app = FastAPI()
test_app.include_router(router, prefix="/offers")

# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
ADMIN_PROFILE = {"id": "admin-1", "role": "admin", "managed_supermarket_id": None}
MANAGER_PROFILE = {"id": "mgr-1", "role": "supermarket_manager", "managed_supermarket_id": "sup-1"}
MANAGER_OTHER_PROFILE = {"id": "mgr-2", "role": "supermarket_manager", "managed_supermarket_id": "sup-other"}

VALID_PAYLOAD = {
    "supermarket_id": "sup-1",
    "name": "Mozzarella",
    "brand": "Galbani",
    "price_offer": 1.99,
}


async def _post(url: str, dep_overrides: dict, json: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, json=json)


async def _get(url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


def _make_sb(supermarket_data: dict | None = None) -> MagicMock:
    """Build a Supabase mock for the offers router."""
    sb = MagicMock()

    # supermarket lookup
    sm_result = MagicMock()
    sm_result.data = supermarket_data if supermarket_data is not None else {"id": "sup-1", "name": "Coop"}
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = sm_result

    # offer insert
    insert_result = MagicMock()
    insert_result.data = [{"id": "offer-new"}]
    sb.table.return_value.insert.return_value.execute.return_value = insert_result

    # offer select after insert
    final_result = MagicMock()
    final_result.data = {
        "id": "offer-new",
        "flyer_id": None,
        "supermarket_id": "sup-1",
        "supermarket_name": "Coop",
        "name": "Mozzarella",
        "brand": "Galbani",
        "category": None,
        "subcategory": None,
        "format": {},
        "format_label": "",
        "image_url": None,
        "price_offer": 1.99,
        "price_original": None,
        "unit_price": None,
        "unit_price_value": None,
        "unit_price_unit": None,
        "offer_notes": None,
        "valid_from": None,
        "valid_to": None,
        "is_confirmed": False,
    }
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = final_result

    return sb


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_public_offers_filters_by_nearby_supermarkets():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(data=[{"id": "sup-1"}])
    query = sb.table.return_value.select.return_value.eq.return_value.eq.return_value
    query.in_.return_value.order.return_value.range.return_value.execute.return_value = MagicMock(
        data=[], count=0
    )

    with patch("api.routers.offers.get_supabase", return_value=sb), patch(
        "api.routers.offers.apply_current_offer_window", side_effect=lambda value: value
    ):
        resp = await _get("/offers?lat=45.464&lng=9.189&max_distance_km=10")

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "nextPage": None}
    sb.rpc.assert_called_once_with(
        "nearby_supermarkets",
        {"user_lat": 45.464, "user_lng": 9.189, "radius_m": 10000.0},
    )
    query.in_.assert_called_once_with("supermarket_id", ["sup-1"])


def test_supermarket_address_keeps_only_city_for_city_only_location():
    assert _offers_module._supermarket_address(
        {
            "name": "Conad Superstore",
            "address": "Conad Superstore - Taurianova",
            "city": "Taurianova",
        },
        "Conad Superstore",
    ) == "Taurianova"


def test_supermarket_address_keeps_street_and_city():
    assert _offers_module._supermarket_address(
        {"address": "Via Roma 12", "city": "Milano"}, None
    ) == "Via Roma 12, Milano"

class TestCreateManualOffer:
    @pytest.mark.asyncio
    async def test_create_manual_offer_as_admin(self):
        sb = _make_sb()
        with patch("api.routers._offer_utils.get_supabase", return_value=sb), \
             patch("api.routers.offers.get_supabase", return_value=sb):
            resp = await _post(
                "/offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json=VALID_PAYLOAD,
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Mozzarella"
        assert data["is_confirmed"] is False

    @pytest.mark.asyncio
    async def test_create_manual_offer_as_manager_own_supermarket(self):
        sb = _make_sb()
        with patch("api.routers._offer_utils.get_supabase", return_value=sb), \
             patch("api.routers.offers.get_supabase", return_value=sb):
            resp = await _post(
                "/offers",
                {_DEP_PROFILE: lambda: MANAGER_PROFILE},
                json=VALID_PAYLOAD,
            )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_manual_offer_as_manager_foreign_supermarket(self):
        sb = _make_sb()
        with patch("api.routers._offer_utils.get_supabase", return_value=sb), \
             patch("api.routers.offers.get_supabase", return_value=sb):
            resp = await _post(
                "/offers",
                {_DEP_PROFILE: lambda: MANAGER_OTHER_PROFILE},
                json=VALID_PAYLOAD,
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_manual_offer_supermarket_not_found(self):
        sb = _make_sb(supermarket_data=None)
        # Override: maybe_single returns no data
        sm_result = MagicMock()
        sm_result.data = None
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = sm_result
        with patch("api.routers._offer_utils.get_supabase", return_value=sb), \
             patch("api.routers.offers.get_supabase", return_value=sb):
            resp = await _post(
                "/offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json=VALID_PAYLOAD,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_manual_offer_invalid_price(self):
        sb = _make_sb()
        with patch("api.routers._offer_utils.get_supabase", return_value=sb), \
             patch("api.routers.offers.get_supabase", return_value=sb):
            resp = await _post(
                "/offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json={**VALID_PAYLOAD, "price_offer": -1.0},
            )
        assert resp.status_code == 422
