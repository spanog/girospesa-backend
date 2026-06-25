from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_db_mod = types.ModuleType("core.database")
_db_mod.get_supabase = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.database"] = _db_mod

_auth_mod = types.ModuleType("core.auth")
_auth_mod.require_admin = MagicMock()  # type: ignore[attr-defined]
_auth_mod.require_admin_or_manager = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.auth"] = _auth_mod

_config_mod = types.ModuleType("core.config")
_config_mod.settings = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod

from fastapi import FastAPI
import httpx
import pytest

import api.routers.admin_products as _admin_products_module
from api.routers.admin_products import router

_DEP_REQUIRE_ADMIN = _admin_products_module.require_admin

test_app = FastAPI()
test_app.include_router(router, prefix="/admin/products")


def _admin_dep():
    return {"id": "admin-1", "app_metadata": {"role": "admin"}}


async def _get(url: str) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def _delete(url: str) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.delete(url)


async def _patch(url: str, json: dict) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(url, json=json)


def _query(data=None) -> MagicMock:
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.single.return_value = query
    query.order.return_value = query
    query.execute.return_value = MagicMock(data=data)
    return query


@pytest.mark.asyncio
async def test_list_products_applies_category_and_subcategory_filters():
    sb = MagicMock()
    select_query = sb.table.return_value.select.return_value
    range_query = select_query.eq.return_value.order.return_value.range.return_value
    range_query.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _get(
            "/admin/products?category=dispensa&subcategory=Primi%20Piatti%20e%20Preparati"
        )

    assert resp.status_code == 200
    select_query.eq.assert_any_call("is_archived", False)
    range_query.eq.assert_any_call("category", "dispensa")
    range_query.eq.return_value.eq.assert_any_call(
        "subcategory", "Primi Piatti e Preparati"
    )


@pytest.mark.asyncio
async def test_list_products_without_subcategory_keeps_current_behavior():
    sb = MagicMock()
    select_query = sb.table.return_value.select.return_value
    range_query = select_query.eq.return_value.order.return_value.range.return_value
    range_query.eq.return_value.execute.return_value = MagicMock(data=[])

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _get("/admin/products?category=dispensa")

    assert resp.status_code == 200
    select_query.eq.assert_any_call("is_archived", False)
    range_query.eq.assert_any_call("category", "dispensa")
    assert ("subcategory", "Primi Piatti e Preparati") not in [
        call.args for call in range_query.eq.return_value.eq.call_args_list
    ]


@pytest.mark.asyncio
async def test_delete_product_returns_409_when_product_has_linked_offers():
    sb = MagicMock()
    product_query = sb.table.return_value.select.return_value.eq.return_value.single.return_value
    product_query.execute.return_value = MagicMock(
        data={"id": "product-1", "name": "Pasta", "is_archived": True}
    )
    offers_query = sb.table.return_value.select.return_value.eq.return_value.limit.return_value
    offers_query.execute.return_value = MagicMock(data=[{"id": "offer-1"}])

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _delete("/admin/products/product-1")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Prodotto con offerte collegate: archivia invece di eliminare."


@pytest.mark.asyncio
async def test_delete_product_removes_favorites_then_product_when_no_offers():
    sb = MagicMock()
    product_query = sb.table.return_value.select.return_value.eq.return_value.single.return_value
    product_query.execute.return_value = MagicMock(
        data={"id": "product-1", "name": "Pasta", "is_archived": True}
    )
    offers_query = sb.table.return_value.select.return_value.eq.return_value.limit.return_value
    offers_query.execute.return_value = MagicMock(data=[])

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _delete("/admin/products/product-1")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    sb.table.assert_any_call("favorites")
    sb.table.return_value.delete.return_value.eq.assert_any_call("product_id", "product-1")
    sb.table.assert_any_call("products")
    sb.table.return_value.delete.return_value.eq.assert_any_call("id", "product-1")


@pytest.mark.asyncio
async def test_delete_product_returns_404_when_missing():
    sb = MagicMock()
    product_query = sb.table.return_value.select.return_value.eq.return_value.single.return_value
    product_query.execute.return_value = MagicMock(data=None)

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _delete("/admin/products/missing")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Prodotto non trovato"


@pytest.mark.asyncio
async def test_get_product_returns_only_published_target_offers():
    sb = MagicMock()
    product_table = MagicMock()
    offers_table = MagicMock()
    product_query = _query({"id": "product-1", "name": "Pasta"})
    offers_query = _query([])
    product_table.select.return_value = product_query
    offers_table.select.return_value = offers_query
    sb.table.side_effect = lambda name: {
        "products": product_table,
        "offers": offers_table,
    }[name]

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _get("/admin/products/product-1")

    assert resp.status_code == 200
    offers_query.eq.assert_any_call("product_id", "product-1")
    offers_query.eq.assert_any_call("offer_kind", "published_target")


@pytest.mark.asyncio
async def test_update_offer_accepts_structured_format():
    sb = MagicMock()
    offers_table = MagicMock()
    existing_query = _query({"id": "offer-1", "product_id": "product-1"})
    update_query = _query([{"id": "offer-1", "format_label": "500 g"}])
    offers_table.select.return_value = existing_query
    offers_table.update.return_value = update_query
    sb.table.return_value = offers_table

    payload = {
        "price_offer": 1.99,
        "format": {
            "tipo": "confezione_singola",
            "peso_volume": 500,
            "unita_misura": "g",
        },
    }

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _patch("/admin/products/product-1/offers/offer-1", payload)

    assert resp.status_code == 200
    updates = offers_table.update.call_args.args[0]
    assert updates["format"] == {
        "tipo": "confezione_singola",
        "peso_volume": 500.0,
        "unita_misura": "g",
    }
    assert updates["format_label"] == "500 g"
    assert updates["format_key"] == (
        'v1:{"peso_volume":500.0,"tipo":"confezione_singola","unita_misura":"g"}'
    )
