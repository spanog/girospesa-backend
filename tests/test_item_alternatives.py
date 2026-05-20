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
sys.modules["core.database"] = MagicMock()
_auth = types.ModuleType("core.auth")
_auth.get_current_user_id = MagicMock()
sys.modules["core.auth"] = _auth

import httpx
import pytest
from fastapi import FastAPI

from api.routers import lists as _lists_module
from api.routers.lists import router as _lists_router

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
        if name == "list_members":
            return member_q
        if name == "offers":
            return offers_q
        return MagicMock()

    sb.table.side_effect = _table
    return sb


@pytest.mark.asyncio
async def test_returns_alternatives_by_product_id():
    items = [
        {
            "id": "item-1",
            "name": "Actimel",
            "quantity": 1,
            "purchased": False,
            "pinned_product_id": "prod-1",
            "pinned_offer_id": "off-1",
            "found_deals": [],
        }
    ]
    offers = [
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
            "format": {},
            "format_label": "",
            "products": {"name": "Actimel", "brand": None},
            "supermarkets": {"name": "Diper"},
        },
    ]
    sb = _make_sb(items, offers)
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=None):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/items/item-1/alternatives")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["offer_id"] == "off-2"
    assert data[0]["supermarket_name"] == "Diper"


@pytest.mark.asyncio
async def test_excludes_current_pinned_offer():
    items = [
        {
            "id": "item-1",
            "name": "Actimel",
            "quantity": 1,
            "purchased": False,
            "pinned_product_id": "prod-1",
            "pinned_offer_id": "off-1",
            "found_deals": [],
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
            "valid_to": None,
            "format": {},
            "format_label": "",
            "products": {"name": "Actimel", "brand": None},
            "supermarkets": {"name": "Conad"},
        },
    ]
    sb = _make_sb(items, offers)
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=None):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/items/item-1/alternatives")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_returns_404_for_missing_item():
    items = [
        {
            "id": "item-1",
            "name": "x",
            "quantity": 1,
            "purchased": False,
            "pinned_product_id": None,
            "pinned_offer_id": None,
            "found_deals": [],
        }
    ]
    sb = _make_sb(items, [])
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=None):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/items/nonexistent/alternatives")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_returns_supermarket_logo_url():
    items = [
        {
            "id": "item-1",
            "name": "Actimel",
            "quantity": 1,
            "purchased": False,
            "pinned_product_id": "prod-1",
            "pinned_offer_id": "off-1",
            "found_deals": [],
        }
    ]
    offers = [
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
            "format": {},
            "format_label": "",
            "products": {"name": "Actimel", "brand": None},
            "supermarkets": {"name": "Diper", "logo_url": "https://cdn.example.com/diper.png"},
        },
    ]
    sb = _make_sb(items, offers)
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=None):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/items/item-1/alternatives")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["supermarket_logo_url"] == "https://cdn.example.com/diper.png"


@pytest.mark.asyncio
async def test_returns_null_logo_url_when_missing():
    items = [
        {
            "id": "item-1",
            "name": "Actimel",
            "quantity": 1,
            "purchased": False,
            "pinned_product_id": "prod-1",
            "pinned_offer_id": "off-1",
            "found_deals": [],
        }
    ]
    offers = [
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
            "format": {},
            "format_label": "",
            "products": {"name": "Actimel", "brand": None},
            "supermarkets": {"name": "Diper", "logo_url": None},
        },
    ]
    sb = _make_sb(items, offers)
    with patch.object(_lists_module, "get_supabase", return_value=sb):
        with patch.object(_lists_module, "load_nearby_distances", return_value=None):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/lists/{_LIST}/items/item-1/alternatives")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["supermarket_logo_url"] is None
