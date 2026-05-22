"""Unit tests for public product endpoints.

Tests verify:
- List endpoint returns paginated flattened offers and enforces confirmed+active filters
- Detail endpoint returns flattened offer+product+supermarket data
- Detail endpoint returns 404 when offer not found
- Similar endpoint excludes the current offer and its supermarket
- Similar endpoint returns empty list when offer not found
"""

from __future__ import annotations

import sys
import os
import types
from unittest.mock import ANY, MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub infrastructure modules
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders", "rapidfuzz"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_config_mod.settings = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

_extraction_pkg = types.ModuleType("services.extraction")
_normalizer_mod = types.ModuleType("services.extraction.normalizer")


def _format_unit_price_label(value, unit):
    if value is None or not unit:
        return None
    return f"{value:.2f} €/{unit}".replace(".", ",")


_normalizer_mod.format_unit_price_label = _format_unit_price_label  # type: ignore[attr-defined]
_normalizer_mod.normalize_products = lambda products: products  # type: ignore[attr-defined]
_normalizer_mod.deduplicate_products = lambda products: products  # type: ignore[attr-defined]
sys.modules["services.extraction"] = _extraction_pkg
sys.modules["services.extraction.normalizer"] = _normalizer_mod

from fastapi import FastAPI
import httpx
import pytest

from api.routers.products import router

test_app = FastAPI()
test_app.include_router(router, prefix="/products")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OFFER_ROW = {
    "id": "offer-1",
    "product_id": "prod-1",
    "supermarket_id": "sm-1",
    "supermarket_name": "Lidl",
    "price_offer": 1.99,
    "price_original": 2.99,
    "discount_pct": 33,
    "valid_from": "2026-04-01",
    "valid_to": "2026-04-30",
    "is_active": True,
    "offer_type": "sconto-diretto",
    "offer_notes": None,
    "unit_price": None,
    "unit_price_value": None,
    "unit_price_unit": None,
    "flyer_id": "flyer-1",
    "raw_text": None,
    "confidence_score": None,
    "created_at": "2026-04-01T00:00:00Z",
    "products": {
        "id": "prod-1",
        "name": "Latte intero",
        "brand": "Milbona",
        "category": "latticini-uova",
        "subcategory": None,
        "format": "1L",
        "image_url": None,
    },
    "supermarkets": {
        "name": "Lidl",
        "slug": "lidl",
        "logo_url": "https://storage.example.com/logos/lidl.png",
        "color_hex": "#0050AA",
        "address": "Via Roma 12",
        "city": "Milano",
    },
}

_SIMILAR_ROW = {
    **_OFFER_ROW,
    "id": "offer-2",
    "supermarket_id": "sm-2",
    "supermarket_name": "Esselunga",
    "price_offer": 1.79,
    "unit_price": "1,79 €/l",
    "unit_price_value": 1.79,
    "unit_price_unit": "l",
    "products": {**_OFFER_ROW["products"]},  # type: ignore[dict-item]
    "supermarkets": {
        "name": "Esselunga",
        "slug": "esselunga",
        "logo_url": "https://storage.example.com/logos/esselunga.png",
        "color_hex": "#E30613",
        "address": "Via Roma 12",
        "city": "Milano",
    },
}


_LIST_ROW = {
    **_OFFER_ROW,
    "id": "offer-list-1",
}


def _make_sb(offer_row=None, similar_rows=None, ref_row=None):
    """Build a Supabase mock for products router tests."""
    sb = MagicMock()
    chain = sb.table.return_value.select.return_value

    # get_product: .select().eq(id).eq(is_confirmed).limit(1).execute()
    chain.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[offer_row] if offer_row is not None else []
    )

    return sb


async def _get(url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


# ---------------------------------------------------------------------------
# Tests — GET /products
# ---------------------------------------------------------------------------


class TestListProducts:
    @pytest.mark.asyncio
    async def test_returns_paginated_confirmed_active_offers(self):
        sb = MagicMock()
        execute_result = MagicMock(data=[_LIST_ROW], count=1)
        select_mock = sb.table.return_value.select.return_value
        eq_mock = select_mock.eq.return_value
        order_mock = eq_mock.order.return_value
        order_mock.range.return_value.execute.return_value = execute_result
        eq_mock.execute.return_value = MagicMock(
            data=[{"supermarket_id": "sm-1"}]
        )
        eq_mock.gte.return_value.lte.return_value.execute.return_value = MagicMock(
            data=[],
            count=0,
        )

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products")

        assert resp.status_code == 200
        data = resp.json()
        assert data["nextPage"] is None
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == "offer-list-1"
        assert data["items"][0]["name"] == "Latte intero"
        assert data["items"][0]["supermarket_slug"] == "lidl"

        select_mock.eq.assert_any_call("is_confirmed", True)
        select_mock.eq.return_value.order.assert_called_once_with(
            "name",
            desc=False,
            foreign_table="products",
        )

    @pytest.mark.asyncio
    async def test_excludes_future_start_offers(self):
        sb = MagicMock()
        execute_result = MagicMock(data=[], count=0)
        select_mock = sb.table.return_value.select.return_value
        eq_mock = select_mock.eq.return_value
        order_mock = eq_mock.order.return_value
        order_mock.range.return_value.execute.return_value = execute_result
        eq_mock.execute.return_value = MagicMock(
            data=[]
        )
        eq_mock.gte.return_value.lte.return_value.execute.return_value = MagicMock(
            data=[],
            count=0,
        )

        with (
            patch("api.routers.products.get_supabase", return_value=sb),
            patch(
                "api.routers.products.apply_current_offer_window",
                side_effect=lambda query, today=None: query,
            ) as apply_window,
        ):
            resp = await _get("/products")

        assert resp.status_code == 200
        order_mock.range.assert_called_once()
        apply_window.assert_any_call(order_mock)
        apply_window.assert_any_call(eq_mock)
        apply_window.assert_any_call(eq_mock, today=ANY)

    @pytest.mark.asyncio
    async def test_expiry_sort_orders_by_valid_to_then_product_name(self):
        sb = MagicMock()
        execute_result = MagicMock(data=[_LIST_ROW], count=1)
        select_mock = sb.table.return_value.select.return_value
        eq_mock = select_mock.eq.return_value
        order_mock = eq_mock.order.return_value
        second_order_mock = order_mock.order.return_value
        second_order_mock.range.return_value.execute.return_value = execute_result
        eq_mock.execute.return_value = MagicMock(
            data=[]
        )
        eq_mock.gte.return_value.lte.return_value.execute.return_value = MagicMock(
            data=[],
            count=0,
        )

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products?sort=expiry")

        assert resp.status_code == 200
        eq_mock.order.assert_called_once_with(
            "valid_to",
            desc=False,
            nullsfirst=False,
        )
        order_mock.order.assert_called_once_with(
            "name",
            desc=False,
            foreign_table="products",
        )

    @pytest.mark.asyncio
    async def test_expiring_soon_filter_applies_today_to_cutoff_window(self):
        sb = MagicMock()
        execute_result = MagicMock(data=[_LIST_ROW], count=1)
        select_mock = sb.table.return_value.select.return_value
        eq_mock = select_mock.eq.return_value
        order_mock = eq_mock.order.return_value
        order_mock.gte.return_value.lte.return_value.range.return_value.execute.return_value = (
            execute_result
        )
        eq_mock.execute.return_value = MagicMock(data=[{"supermarket_id": "sm-1"}])
        eq_mock.gte.return_value.lte.return_value.execute.return_value = MagicMock(
            data=[],
            count=1,
        )

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products?expiring_soon=true")

        assert resp.status_code == 200
        order_mock.gte.assert_called_once()
        order_mock.gte.return_value.lte.assert_called_once()
        order_mock.gte.return_value.lte.return_value.range.assert_called_once()

    @pytest.mark.asyncio
    async def test_distance_filtering_restricts_to_nearby_supermarkets(self):
        """When lat/lng provided, only supermarkets within max_distance_km are included."""
        offers_chain = MagicMock()
        execute_result = MagicMock(data=[_LIST_ROW], count=1)
        select_mock = offers_chain.select.return_value
        eq_mock = select_mock.eq.return_value
        order_mock = eq_mock.order.return_value
        order_mock.in_.return_value.range.return_value.execute.return_value = (
            execute_result
        )
        eq_mock.in_.return_value.execute.return_value = MagicMock(
            data=[{"supermarket_id": "sm-1"}]
        )
        eq_mock.gte.return_value.lte.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[],
            count=0,
        )

        sb = MagicMock()
        sb.table.return_value = offers_chain
        sb.rpc.return_value.execute.return_value = MagicMock(
            data=[{"id": "sm-1", "distance_km": 1.2}]
        )

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products?lat=45.464&lng=9.189&max_distance_km=10")

        assert resp.status_code == 200
        order_mock.in_.assert_called_once_with("supermarket_id", ["sm-1"])

    @pytest.mark.asyncio
    async def test_distance_filtering_returns_empty_when_no_nearby_supermarkets(self):
        """Returns empty items when no supermarkets fall within the radius."""
        offers_chain = MagicMock()
        sb = MagicMock()
        sb.table.return_value = offers_chain
        sb.rpc.return_value.execute.return_value = MagicMock(data=[])

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products?lat=45.464&lng=9.189&max_distance_km=10")

        assert resp.status_code == 200
        assert resp.json() == {
            "items": [],
            "nextPage": None,
            "total": 0,
            "supermarket_count": 0,
            "expiring_soon_count": 0,
        }
        # Offers query should never be executed when nearby_ids is empty
        offers_chain.select.return_value.eq.return_value.order.return_value.range.return_value.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_distance_filtering_uses_postgis_rpc(self):
        """When lat/lng provided, nearby supermarket filtering runs in PostGIS."""
        offers_chain = MagicMock()
        execute_result = MagicMock(data=[_LIST_ROW], count=1)
        select_mock = offers_chain.select.return_value
        eq_mock = select_mock.eq.return_value
        order_mock = eq_mock.order.return_value
        order_mock.in_.return_value.range.return_value.execute.return_value = (
            execute_result
        )
        eq_mock.in_.return_value.execute.return_value = MagicMock(
            data=[{"supermarket_id": "sm-1"}]
        )
        eq_mock.gte.return_value.lte.return_value.in_.return_value.execute.return_value = MagicMock(
            data=[],
            count=0,
        )

        sb = MagicMock()
        sb.table.return_value = offers_chain
        sb.rpc.return_value.execute.return_value = MagicMock(
            data=[{"id": "sm-1", "distance_km": 1.2}]
        )

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products?lat=45.464&lng=9.189&max_distance_km=10")

        assert resp.status_code == 200
        sb.rpc.assert_called_once_with(
            "nearby_supermarkets",
            {
                "user_lat": 45.464,
                "user_lng": 9.189,
                "radius_m": 10000.0,
            },
        )
        assert sb.table.call_args_list == [call("offers"), call("offers"), call("offers")]
        order_mock.in_.assert_called_once_with("supermarket_id", ["sm-1"])


# ---------------------------------------------------------------------------
# Tests — GET /products/{product_id}
# ---------------------------------------------------------------------------


class TestGetProduct:
    @pytest.mark.asyncio
    async def test_returns_flattened_offer(self):
        sb = _make_sb(offer_row=_OFFER_ROW)

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products/offer-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "offer-1"
        assert data["name"] == "Latte intero"
        assert data["brand"] == "Milbona"
        assert data["supermarket_name"] == "Lidl"
        assert data["supermarket_logo_url"] == "https://storage.example.com/logos/lidl.png"
        assert data["supermarket_slug"] == "lidl"
        assert data["supermarket_address"] == "Via Roma 12, Milano"
        assert data["product_id"] == "prod-1"
        assert data["unit_price_label"] is None
        # Nested dicts should NOT be in the response
        assert "products" not in data
        assert "supermarkets" not in data

    @pytest.mark.asyncio
    async def test_returns_none_when_supermarket_address_missing(self):
        row = {
            **_OFFER_ROW,
            "supermarkets": {
                **_OFFER_ROW["supermarkets"],  # type: ignore[dict-item]
                "address": None,
                "city": None,
            },
        }
        sb = _make_sb(offer_row=row)

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products/offer-1")

        assert resp.status_code == 200
        assert resp.json()["supermarket_address"] is None

    @pytest.mark.asyncio
    async def test_returns_address_only_when_city_missing(self):
        row = {
            **_OFFER_ROW,
            "supermarkets": {
                **_OFFER_ROW["supermarkets"],  # type: ignore[dict-item]
                "address": "Via Roma 12",
                "city": None,
            },
        }
        sb = _make_sb(offer_row=row)

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products/offer-1")

        assert resp.status_code == 200
        assert resp.json()["supermarket_address"] == "Via Roma 12"

    @pytest.mark.asyncio
    async def test_returns_city_only_when_address_missing(self):
        row = {
            **_OFFER_ROW,
            "supermarkets": {
                **_OFFER_ROW["supermarkets"],  # type: ignore[dict-item]
                "address": None,
                "city": "Milano",
            },
        }
        sb = _make_sb(offer_row=row)

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products/offer-1")

        assert resp.status_code == 200
        assert resp.json()["supermarket_address"] == "Milano"

    @pytest.mark.asyncio
    async def test_returns_structured_unit_price_fields(self):
        row = {
            **_OFFER_ROW,
            "unit_price": "1,79 €/l",
            "unit_price_value": 1.79,
            "unit_price_unit": "l",
        }
        sb = _make_sb(offer_row=row)

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products/offer-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["unit_price"] == "1,79 €/l"
        assert data["unit_price_value"] == pytest.approx(1.79)
        assert data["unit_price_unit"] == "l"
        assert data["unit_price_label"] == "1,79 €/l"

    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self):
        sb = _make_sb(offer_row=None)

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products/nonexistent")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — GET /products/{product_id}/similar
# ---------------------------------------------------------------------------


class TestGetSimilarProducts:
    @pytest.mark.asyncio
    async def test_returns_similar_offers(self):
        sb = MagicMock()
        ref_select = MagicMock()
        similar_select = MagicMock()
        sb.table.return_value.select.side_effect = [ref_select, similar_select]

        ref_id_query = ref_select.eq.return_value
        ref_confirmed_query = ref_id_query.eq.return_value
        ref_confirmed_query.limit.return_value.execute.return_value = MagicMock(
            data=[{"product_id": "prod-1", "supermarket_id": "sm-1"}]
        )

        similar_product_query = similar_select.eq.return_value
        similar_confirmed_query = similar_product_query.eq.return_value
        similar_offer_exclusion_query = similar_confirmed_query.neq.return_value
        similar_market_exclusion_query = similar_offer_exclusion_query.neq.return_value
        similar_market_exclusion_query.order.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[_SIMILAR_ROW]
        )

        with (
            patch("api.routers.products.get_supabase", return_value=sb),
            patch(
                "api.routers.products.apply_current_offer_window",
                side_effect=lambda query, today=None: query,
            ) as apply_window,
        ):
            resp = await _get("/products/offer-1/similar")

        assert resp.status_code == 200
        results = resp.json()
        assert isinstance(results, list)
        assert results[0]["supermarket_address"] == "Via Roma 12, Milano"
        ref_select.eq.assert_called_once_with("id", "offer-1")
        ref_id_query.eq.assert_called_once_with("is_confirmed", True)
        ref_confirmed_query.limit.assert_called_once_with(1)
        similar_select.eq.assert_called_once_with("product_id", "prod-1")
        similar_product_query.eq.assert_called_once_with("is_confirmed", True)
        similar_confirmed_query.neq.assert_called_once_with("id", "offer-1")
        similar_offer_exclusion_query.neq.assert_called_once_with("supermarket_id", "sm-1")
        apply_window.assert_has_calls(
            [
                call(ref_confirmed_query),
                call(similar_market_exclusion_query.order.return_value),
            ]
        )

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_offer_not_found(self):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[]
        )

        with patch("api.routers.products.get_supabase", return_value=sb):
            resp = await _get("/products/nonexistent/similar")

        assert resp.status_code == 200
        assert resp.json() == []
