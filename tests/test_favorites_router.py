"""Unit tests for api/routers/favorites.py.

Tests verify:
- GET /favorites returns favorite products flattened with best_offer payload
- GET /favorites returns active_offers sorted by price
- GET /favorites/{product_id} returns is_favorite=True when row exists
- GET /favorites/{product_id} returns is_favorite=False when no row
- POST /favorites creates a row and returns it
- DELETE /favorites/{product_id} deletes the row and returns 204
"""

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
_config_mod.settings = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

_auth_mod = types.ModuleType("core.auth")
_auth_mod.get_current_user_id = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.auth"] = _auth_mod

from fastapi import FastAPI
import httpx
import pytest

import api.routers.favorites as _fav_module
from api.routers.favorites import router

_DEP_GET_USER_ID = _fav_module.get_current_user_id  # stable ref for override

test_app = FastAPI()
test_app.include_router(router, prefix="/favorites")

USER_ID = "user-abc"
PRODUCT_ID = "prod-xyz"


def _deps(user_id: str = USER_ID) -> dict:
    return {_DEP_GET_USER_ID: lambda: user_id}


async def _get(url: str, dep_overrides: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides or {}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def _post(url: str, dep_overrides: dict | None = None, json: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides or {}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, json=json)


async def _delete(url: str, dep_overrides: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides or {}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.delete(url)


# ---------------------------------------------------------------------------
# Tests — GET /favorites
# ---------------------------------------------------------------------------


class TestListFavorites:
    @pytest.mark.asyncio
    async def test_returns_best_offer_with_active_offers(self):
        sb = MagicMock()

        favorites_resp = MagicMock(data=[{
            "product_id": PRODUCT_ID,
            "id": "fav-1",
            "products": {
                "id": PRODUCT_ID,
                "name": "Pasta",
                "brand": "Barilla",
                "image_url": None,
                "category": "dispensa",
                "subcategory": "Pasta",
            },
        }])
        offer_resp = MagicMock(data=[
            {
                "id": "offer-1",
                "price_offer": 0.99,
                "price_original": 1.49,
                "discount_pct": 34,
                "valid_to": "2099-12-31",
                "created_at": "2026-04-01T00:00:00Z",
                "format": {"tipo": "confezione_singola", "peso_volume": 500, "unita_misura": "g"},
                "format_label": "500 g",
                "supermarket_name": "Lidl",
                "supermarket_id": "sup-1",
                "supermarkets": {
                    "logo_url": "https://example.com/lidl.png",
                    "address": "Via Roma 10, Milano",
                },
                "unit_price": "1,98 €/kg",
                "unit_price_value": 1.98,
                "unit_price_unit": "kg",
            },
            {
                "id": "offer-2",
                "price_offer": 1.09,
                "price_original": 1.59,
                "discount_pct": 31,
                "valid_to": "2099-12-30",
                "created_at": "2026-03-20T00:00:00Z",
                "format": {"tipo": "confezione_singola", "peso_volume": 550, "unita_misura": "g"},
                "format_label": "550 g",
                "supermarket_name": "Coop",
                "supermarket_id": "sup-2",
                "supermarkets": {
                    "logo_url": "https://example.com/coop.png",
                    "address": "Corso Italia 20, Milano",
                },
                "unit_price": None,
                "unit_price_value": 2.18,
                "unit_price_unit": "kg",
            },
        ])

        table = sb.table.return_value
        table.select.return_value.eq.return_value.execute.return_value = favorites_resp
        (
            table.select.return_value
            .eq.return_value.eq.return_value
            .order.return_value
            .order.return_value.execute.return_value
        ) = offer_resp

        with patch("api.routers.favorites.get_supabase", return_value=sb):
            resp = await _get("/favorites", _deps())

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["favorite_id"] == "fav-1"
        assert data[0]["product_id"] == PRODUCT_ID
        assert data[0]["subcategory"] == "Pasta"
        assert data[0]["format_label"] == "500 g"
        assert data[0]["format"]["peso_volume"] == 500
        assert data[0]["best_offer"]["offer_id"] == "offer-1"
        assert data[0]["best_offer"]["unit_price_value"] == pytest.approx(1.98)
        assert data[0]["best_offer"]["unit_price_unit"] == "kg"
        assert data[0]["best_offer"]["unit_price_label"] == "1,98 €/kg"
        assert data[0]["best_offer"]["supermarket_address"] == "Via Roma 10, Milano"
        assert [offer["offer_id"] for offer in data[0]["active_offers"]] == [
            "offer-1",
            "offer-2",
        ]
        assert data[0]["active_offers"][1]["unit_price_label"] == "2,18 €/kg"


# ---------------------------------------------------------------------------
# Tests — GET /favorites/{product_id}
# ---------------------------------------------------------------------------


class TestCheckFavorite:
    @pytest.mark.asyncio
    async def test_returns_true_when_row_exists(self):
        sb = MagicMock()
        (
            sb.table.return_value.select.return_value
            .eq.return_value.eq.return_value.execute.return_value
        ) = MagicMock(data=[{"id": "fav-1"}])

        with patch("api.routers.favorites.get_supabase", return_value=sb):
            resp = await _get(f"/favorites/{PRODUCT_ID}", _deps())

        assert resp.status_code == 200
        assert resp.json() == {"is_favorite": True}

    @pytest.mark.asyncio
    async def test_returns_false_when_no_row(self):
        sb = MagicMock()
        (
            sb.table.return_value.select.return_value
            .eq.return_value.eq.return_value.execute.return_value
        ) = MagicMock(data=[])

        with patch("api.routers.favorites.get_supabase", return_value=sb):
            resp = await _get(f"/favorites/{PRODUCT_ID}", _deps())

        assert resp.status_code == 200
        assert resp.json() == {"is_favorite": False}


# ---------------------------------------------------------------------------
# Tests — POST /favorites
# ---------------------------------------------------------------------------


class TestAddFavorite:
    @pytest.mark.asyncio
    async def test_upserts_and_returns_row(self):
        new_row = {"id": "fav-1", "user_id": USER_ID, "product_id": PRODUCT_ID}
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(
            data=[new_row]
        )

        with patch("api.routers.favorites.get_supabase", return_value=sb):
            resp = await _post("/favorites", _deps(), json={"product_id": PRODUCT_ID})

        assert resp.status_code == 201
        assert resp.json()["product_id"] == PRODUCT_ID

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        resp = await _post("/favorites", dep_overrides={}, json={"product_id": PRODUCT_ID})
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# Tests — DELETE /favorites/{product_id}
# ---------------------------------------------------------------------------


class TestRemoveFavorite:
    @pytest.mark.asyncio
    async def test_returns_204(self):
        sb = MagicMock()
        (
            sb.table.return_value.delete.return_value
            .eq.return_value.eq.return_value.execute.return_value
        ) = MagicMock(data=[])

        with patch("api.routers.favorites.get_supabase", return_value=sb):
            resp = await _delete(f"/favorites/{PRODUCT_ID}", _deps())

        assert resp.status_code == 204
