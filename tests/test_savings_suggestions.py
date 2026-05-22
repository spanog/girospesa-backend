from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in (
    "supabase",
    "jose",
    "jose.jwt",
    "geopy",
    "geopy.geocoders",
    "httpx",
    "apscheduler",
    "apscheduler.schedulers.asyncio",
    "apscheduler.triggers.cron",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config = types.ModuleType("core.config")
_config.settings = MagicMock()
sys.modules["core.config"] = _config
_database = types.ModuleType("core.database")
_database.has_direct_postgres = lambda: False  # type: ignore[attr-defined]
_database.get_postgres_cursor = MagicMock()  # type: ignore[attr-defined]
_database.get_supabase = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.database"] = _database
_auth = types.ModuleType("core.auth")
_auth.get_current_user_id = MagicMock()
sys.modules["core.auth"] = _auth

import httpx
import pytest
from fastapi import FastAPI

from api.routers import lists as _lists_module
from api.routers.lists import router as _lists_router

_lists_module.repo.has_direct_postgres = lambda: False

_DEP = _lists_module.get_current_user_id
_USER = "user-1"
_LIST = "list-1"


def _app():
    app = FastAPI()
    app.include_router(_lists_router, prefix="/lists")
    app.dependency_overrides[_DEP] = lambda: _USER
    return app


def _make_sb(items, offers):
    sb = MagicMock()
    list_q = MagicMock()
    list_q.select.return_value = list_q
    list_q.eq.return_value = list_q
    list_q.single.return_value = list_q
    list_q.execute.return_value = MagicMock(data={"items": items})

    profile_q = MagicMock()
    profile_q.select.return_value = profile_q
    profile_q.eq.return_value = profile_q
    profile_q.maybe_single.return_value = profile_q
    profile_q.execute.return_value = MagicMock(data=None)

    member_q = MagicMock()
    member_q.select.return_value = member_q
    member_q.eq.return_value = member_q
    member_q.execute.return_value = MagicMock(data=[{"id": "m1"}])

    offers_q = MagicMock()
    offers_q.select.return_value = offers_q
    offers_q.eq.return_value = offers_q
    offers_q.lte.return_value = offers_q
    offers_q.gte.return_value = offers_q
    offers_q.or_.return_value = offers_q
    offers_q.in_.return_value = offers_q
    offers_q.execute.return_value = MagicMock(data=offers)

    def _table(name):
        if name == "shopping_lists":
            return list_q
        if name == "user_profiles":
            return profile_q
        if name == "list_members":
            return member_q
        if name == "offers":
            return offers_q
        return MagicMock()

    sb.table.side_effect = _table
    return sb


@pytest.mark.asyncio
async def test_no_suggestions_when_no_cheaper_offer():
    items = [
        {
            "id": "item-1",
            "name": "Latte",
            "quantity": 1,
            "purchased": False,
            "pinned_product_id": "prod-1",
            "pinned_offer_id": "off-1",
            "found_deals": [
                {
                    "offer_id": "off-1",
                    "supermarket_id": "sup-1",
                    "supermarket_name": "Conad",
                    "price_offer": 1.29,
                }
            ],
        }
    ]
    offers = [
        {
            "id": "off-1",
            "product_id": "prod-1",
            "supermarket_id": "sup-1",
            "price_offer": 1.29,
            "price_original": None,
            "discount_pct": None,
            "unit_price": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "valid_to": None,
            "products": {"name": "Latte"},
            "supermarkets": {"name": "Conad"},
        }
    ]
    sb = _make_sb(items, offers)
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=None):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/savings-suggestions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_returns_suggestion_when_cheaper_offer_exists():
    items = [
        {
            "id": "item-1",
            "name": "Actimel",
            "quantity": 1,
            "purchased": False,
            "pinned_product_id": "prod-1",
            "pinned_offer_id": "off-1",
            "found_deals": [
                {
                    "offer_id": "off-1",
                    "supermarket_id": "sup-1",
                    "supermarket_name": "Conad",
                    "price_offer": 2.39,
                }
            ],
        }
    ]
    offers = [
        {
            "id": "off-1",
            "product_id": "prod-1",
            "supermarket_id": "sup-1",
            "price_offer": 2.39,
            "price_original": None,
            "discount_pct": None,
            "unit_price": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "valid_to": "2026-05-31",
            "products": {"name": "Actimel"},
            "supermarkets": {"name": "Conad"},
        },
        {
            "id": "off-2",
            "product_id": "prod-1",
            "supermarket_id": "sup-2",
            "price_offer": 2.10,
            "price_original": None,
            "discount_pct": None,
            "unit_price": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "valid_to": "2026-05-31",
            "products": {"name": "Actimel"},
            "supermarkets": {"name": "Diper"},
        },
    ]
    sb = _make_sb(items, offers)
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=None):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/savings-suggestions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["item_id"] == "item-1"
    assert data[0]["cheaper_offer_id"] == "off-2"
    assert data[0]["cheaper_price"] == 2.10
    assert data[0]["savings"] == pytest.approx(0.29, abs=0.01)


@pytest.mark.asyncio
async def test_skips_purchased_items():
    items = [
        {
            "id": "item-1",
            "name": "Latte",
            "quantity": 1,
            "purchased": True,
            "pinned_product_id": "prod-1",
            "pinned_offer_id": "off-1",
            "found_deals": [
                {
                    "offer_id": "off-1",
                    "supermarket_id": "sup-1",
                    "supermarket_name": "Conad",
                    "price_offer": 2.39,
                }
            ],
        }
    ]
    sb = _make_sb(items, [])
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=None):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/savings-suggestions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_filters_by_nearby_distances():
    items = [
        {
            "id": "item-1",
            "name": "Actimel",
            "quantity": 1,
            "purchased": False,
            "pinned_product_id": "prod-1",
            "pinned_offer_id": "off-1",
            "found_deals": [
                {
                    "offer_id": "off-1",
                    "supermarket_id": "sup-1",
                    "supermarket_name": "Conad",
                    "price_offer": 2.39,
                }
            ],
        }
    ]
    offers = [
        {
            "id": "off-2",
            "product_id": "prod-1",
            "supermarket_id": "sup-far",
            "price_offer": 1.00,
            "price_original": None,
            "discount_pct": None,
            "unit_price": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "valid_to": None,
            "products": {"name": "Actimel"},
            "supermarkets": {"name": "FarShop"},
        },
    ]
    sb = _make_sb(items, offers)
    nearby = {"sup-1": 1.0}
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=nearby):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/savings-suggestions")
    assert resp.status_code == 200
    assert resp.json() == []
