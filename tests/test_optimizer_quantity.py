"""Tests that the optimizer multiplies subtotal and savings by item quantity."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_config_mod.settings = MagicMock()
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

_auth_mod = types.ModuleType("core.auth")
_auth_mod.get_current_user_id = MagicMock()
sys.modules["core.auth"] = _auth_mod

import httpx
import pytest
from fastapi import FastAPI
import api.routers.optimize as _opt_module
from api.routers.optimize import router as _opt_router

_DEP_GET_USER_ID = _opt_module.get_current_user_id

_test_app = FastAPI()
_test_app.include_router(_opt_router, prefix="/optimize")


def _deps(user_id: str = "user-1") -> dict:
    return {_DEP_GET_USER_ID: lambda: user_id}


def _make_table_mock(items: list[dict], profile: dict, offer: dict) -> MagicMock:
    """Build a supabase mock that returns correct data for shopping_lists and user_profiles queries."""
    list_call = MagicMock()
    list_call.data = {"items": items}
    profile_call = MagicMock()
    profile_call.data = profile

    sb = MagicMock()

    def _table_side_effect(name):
        t = MagicMock()
        if name == "shopping_lists":
            t.select.return_value.eq.return_value.single.return_value.execute.return_value = list_call
        elif name == "user_profiles":
            t.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = profile_call
        return t

    sb.table.side_effect = _table_side_effect
    return sb


async def _run_optimize(items, offer, profile=None) -> dict:
    if profile is None:
        profile = {"home_lat": 38.0, "home_lng": 16.0, "max_distance_km": 10}
    sb = _make_table_mock(items, profile, offer)
    offers_query = MagicMock()
    offers_query.execute.return_value.data = [offer]

    transport = httpx.ASGITransport(app=_test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _test_app.dependency_overrides = _deps()
        with patch.object(_opt_module, "get_supabase", return_value=sb), \
             patch.object(_opt_module, "apply_current_offer_window", return_value=offers_query), \
             patch.object(_opt_module, "_nearby_distances", return_value={"store-1": 1.5}):
            resp = await client.post("/optimize", json={"list_id": "list-1", "mode": "maximize_savings"})

    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
    return resp.json()


def _make_item(quantity: float, name: str = "Latte") -> dict:
    return {
        "id": "item-1",
        "name": name,
        "quantity": quantity,
        "checked": False,
        "purchased": False,
        "pinned_offer_id": None,
        "pinned_product_id": None,
    }


def _make_offer(price_offer: float, price_original: float | None = None) -> dict:
    return {
        "id": "offer-1",
        "product_id": "prod-1",
        "price_offer": price_offer,
        "price_original": price_original,
        "discount_pct": 25 if price_original else None,
        "unit_price": None,
        "unit_price_value": None,
        "unit_price_unit": None,
        "valid_to": "2099-12-31",
        "products": {
            "id": "prod-1", "name": "Latte", "brand": None,
            "format": {}, "format_label": "",
        },
        "supermarkets": {"id": "store-1", "name": "Coop", "logo_url": None},
    }


async def test_subtotal_scales_by_quantity():
    """Item with quantity=2 and price_offer=3.00 → subtotal=6.00."""
    data = await _run_optimize(
        items=[_make_item(2.0)],
        offer=_make_offer(3.00, 4.00),
    )
    assert data["total_cost"] == pytest.approx(6.00, abs=0.01), \
        f"Expected 6.00, got {data['total_cost']}"


async def test_savings_scales_by_quantity():
    """Item with quantity=2, price_offer=3.00, price_original=4.00 → savings=2.00."""
    data = await _run_optimize(
        items=[_make_item(2.0)],
        offer=_make_offer(3.00, 4.00),
    )
    assert data["total_savings"] == pytest.approx(2.00, abs=0.01), \
        f"Expected 2.00 savings, got {data['total_savings']}"


async def test_matched_product_includes_quantity():
    """MatchedProduct in the response must carry quantity=3."""
    data = await _run_optimize(
        items=[_make_item(3.0)],
        offer=_make_offer(2.00),
    )
    product = data["store_groups"][0]["products"][0]
    assert product["quantity"] == 3.0


async def test_quantity_one_baseline():
    """Quantity=1 should behave same as before (no change to baseline)."""
    data = await _run_optimize(
        items=[_make_item(1.0)],
        offer=_make_offer(3.00, 4.00),
    )
    assert data["total_cost"] == pytest.approx(3.00, abs=0.01)
    assert data["total_savings"] == pytest.approx(1.00, abs=0.01)
