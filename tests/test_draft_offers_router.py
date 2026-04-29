"""Unit tests for draft-offers endpoints in api/routers/flyers.py."""

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
# Ensure services.extraction.service is not cached from another stub
# (we patch ExtractionService inside the router endpoint, not the whole module)
for _svc_mod in ("services.extraction.service", "services.extraction", "services.extraction.providers"):
    sys.modules.pop(_svc_mod, None)

from fastapi import FastAPI
import httpx
import pytest

import api.routers.flyers as _flyers_module
from api.routers.flyers import router

_DEP_PROFILE = _flyers_module.require_admin_or_manager
_DEP_USER_ID = _flyers_module.get_current_user_id

test_app = FastAPI()
test_app.include_router(router, prefix="/flyers")

# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
ADMIN_PROFILE = {"id": "admin-1", "role": "admin", "managed_supermarket_id": None}
MANAGER_PROFILE = {"id": "mgr-1", "role": "supermarket_manager", "managed_supermarket_id": "sup-1"}
MANAGER_OTHER_PROFILE = {"id": "mgr-2", "role": "supermarket_manager", "managed_supermarket_id": "sup-other"}


async def _get(url: str, dep_overrides: dict) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def _post(url: str, dep_overrides: dict, json: dict | None = None) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, json=json)


async def _patch_req(url: str, dep_overrides: dict, json: dict) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(url, json=json)


def _sb_with_flyer(flyer: dict | None = None) -> MagicMock:
    sb = MagicMock()
    flyer_data = flyer or {"id": "flyer-1", "supermarket_id": "sup-1", "status": "pending"}
    result = MagicMock()
    result.data = flyer_data
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = result
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    return sb


# ---------------------------------------------------------------------------
# trigger_extraction
# ---------------------------------------------------------------------------

class TestTriggerExtraction:
    @pytest.mark.asyncio
    async def test_pending_returns_202(self):
        sb = _sb_with_flyer({"id": "flyer-1", "supermarket_id": "sup-1", "status": "pending"})
        mock_svc = MagicMock()
        mock_svc.return_value.run = MagicMock()
        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch("services.extraction.service.ExtractionService", mock_svc),
        ):
            resp = await _post(
                "/flyers/flyer-1/extract",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )
        assert resp.status_code == 202
        assert resp.json()["status"] == "processing"

    @pytest.mark.asyncio
    async def test_done_status_returns_409(self):
        sb = _sb_with_flyer({"id": "flyer-1", "supermarket_id": "sup-1", "status": "done"})
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/extract",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_manager_wrong_supermarket_403(self):
        sb = _sb_with_flyer({"id": "flyer-1", "supermarket_id": "sup-1", "status": "pending"})
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/extract",
                {_DEP_PROFILE: lambda: MANAGER_OTHER_PROFILE, _DEP_USER_ID: lambda: "mgr-2"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# create_draft_offer
# ---------------------------------------------------------------------------

class TestCreateDraftOffer:
    def _make_sb(self) -> MagicMock:
        flyers_table = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {
            "id": "flyer-1",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "valid_from": "2026-04-01",
            "valid_to": "2026-04-30",
        }
        flyers_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result

        products_table = MagicMock()
        products_upsert_result = MagicMock()
        products_upsert_result.data = [{"id": "prod-new"}]
        products_table.upsert.return_value.execute.return_value = products_upsert_result

        offers_table = MagicMock()
        insert_result = MagicMock()
        insert_result.data = [{"id": "offer-new"}]
        offers_table.insert.return_value.execute.return_value = insert_result
        final_result = MagicMock()
        final_result.data = {
            "id": "offer-new",
            "flyer_id": "flyer-1",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "price_offer": 1.99,
            "price_original": None,
            "unit_price": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "offer_notes": None,
            "valid_from": "2026-04-01",
            "valid_to": "2026-04-30",
            "is_confirmed": False,
            "products": {
                "id": "prod-new",
                "name": "Mozzarella",
                "brand": "Galbani",
                "category": "latticini",
                "subcategory": "Latticini e Formaggi",
                "format": "125g",
                "image_url": None,
            },
        }
        offers_table.select.return_value.eq.return_value.single.return_value.execute.return_value = final_result

        sb = MagicMock()

        def _dispatch(table_name):
            if table_name == "flyers":
                return flyers_table
            if table_name == "products":
                return products_table
            return offers_table

        sb.table.side_effect = _dispatch
        return sb

    @pytest.mark.asyncio
    async def test_create_returns_201(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/draft-offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json={"name": "Mozzarella", "brand": "Galbani", "price_offer": 1.99},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Mozzarella"
        assert data["is_confirmed"] is False
        assert data["subcategory"] == "Latticini e Formaggi"

    @pytest.mark.asyncio
    async def test_create_inherits_flyer_dates(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/draft-offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json={"name": "Mozzarella", "price_offer": 1.99},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["valid_from"] == "2026-04-01"
        assert data["valid_to"] == "2026-04-30"

    @pytest.mark.asyncio
    async def test_manager_wrong_supermarket_403(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/draft-offers",
                {_DEP_PROFILE: lambda: MANAGER_OTHER_PROFILE},
                json={"name": "Mozzarella", "price_offer": 1.99},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_price_returns_422(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/draft-offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json={"name": "Mozzarella"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_name_returns_422(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/draft-offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json={"price_offer": 1.99},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_persists_subcategory_on_product_upsert(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/draft-offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json={
                    "name": "Mozzarella",
                    "category": "alimentari-freschi",
                    "subcategory": "Latticini e Formaggi",
                    "price_offer": 1.99,
                },
            )

        assert resp.status_code == 201
        dispatch_table = sb.table("products")
        dispatch_table.upsert.assert_called_once_with(
            {
                "name": "Mozzarella",
                "brand": None,
                "category": "alimentari-freschi",
                "subcategory": "Latticini e Formaggi",
                "format": None,
            },
            on_conflict="name,brand,format",
        )


# ---------------------------------------------------------------------------
# list_draft_offers
# ---------------------------------------------------------------------------

class TestListDraftOffers:
    @pytest.mark.asyncio
    async def test_returns_unconfirmed_only(self):
        sb = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {"id": "flyer-1", "supermarket_id": "sup-1"}
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result

        draft_result = MagicMock()
        draft_result.data = [
            {
                "id": "offer-1",
                "flyer_id": "flyer-1",
                "is_confirmed": False,
                "unit_price": "1,29 €/kg",
                "unit_price_value": 1.29,
                "unit_price_unit": "kg",
                "products": {
                    "id": "prod-1",
                    "name": "Pasta",
                    "brand": "Barilla",
                    "category": "dispensa",
                    "subcategory": "Primi Piatti e Preparati",
                    "format": "500g",
                    "image_url": None,
                },
            }
        ]
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = draft_result

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _get(
                "/flyers/flyer-1/draft-offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Pasta"
        assert data[0]["subcategory"] == "Primi Piatti e Preparati"
        assert data[0]["unit_price_label"] == "1,29 €/kg"


# ---------------------------------------------------------------------------
# update_draft_offer
# ---------------------------------------------------------------------------

class TestUpdateDraftOffer:
    def _make_sb(self, is_confirmed: bool = False) -> MagicMock:
        sb = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {"id": "flyer-1", "supermarket_id": "sup-1"}
        offer_result = MagicMock()
        offer_result.data = {
            "id": "offer-1",
            "product_id": "prod-1",
            "flyer_id": "flyer-1",
            "is_confirmed": is_confirmed,
        }
        updated_result = MagicMock()
        updated_result.data = {
            "id": "offer-1",
            "price_offer": 2.99,
            "unit_price": "5,98 €/kg",
            "unit_price_value": 5.98,
            "unit_price_unit": "kg",
            "is_confirmed": False,
            "products": {
                "id": "prod-1",
                "name": "Pasta",
                "brand": "Barilla",
                "category": "dispensa",
                "subcategory": "Primi Piatti e Preparati",
                "format": "500g",
                "image_url": None,
            },
        }
        table = sb.table.return_value
        table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
        table.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = offer_result
        table.select.return_value.eq.return_value.single.return_value.execute.return_value = updated_result
        table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        return sb

    @pytest.mark.asyncio
    async def test_update_price(self):
        sb = self._make_sb(is_confirmed=False)
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _patch_req(
                "/flyers/flyer-1/draft-offers/offer-1",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
                json={"price_offer": 2.99},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_unit_price_fields(self):
        sb = self._make_sb(is_confirmed=False)
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _patch_req(
                "/flyers/flyer-1/draft-offers/offer-1",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
                json={"unit_price_value": 5.98, "unit_price_unit": "kg"},
            )

        assert resp.status_code == 200
        update_calls = sb.table.return_value.update.call_args_list
        assert any(
            c.args[0].get("unit_price_value") == 5.98 and c.args[0].get("unit_price_unit") == "kg"
            for c in update_calls
        )

    @pytest.mark.asyncio
    async def test_confirmed_offer_can_be_edited(self):
        sb = self._make_sb(is_confirmed=True)
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _patch_req(
                "/flyers/flyer-1/draft-offers/offer-1",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
                json={"price_offer": 2.99},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_subcategory_updates_product_fields(self):
        sb = self._make_sb(is_confirmed=False)
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _patch_req(
                "/flyers/flyer-1/draft-offers/offer-1",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
                json={"subcategory": "Snack Salati e Dolciumi"},
            )

        assert resp.status_code == 200
        update_calls = sb.table.return_value.update.call_args_list
        assert any(
            c.args[0].get("subcategory") == "Snack Salati e Dolciumi"
            for c in update_calls
        )


# ---------------------------------------------------------------------------
# confirm_offers
# ---------------------------------------------------------------------------

class TestConfirmOffers:
    def _make_sb(self, flyer_status: str = "done", confirmed_count: int = 3) -> MagicMock:
        sb = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {"id": "flyer-1", "supermarket_id": "sup-1", "status": flyer_status}
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result

        updated_result = MagicMock()
        updated_result.data = [{"id": f"offer-{i}"} for i in range(confirmed_count)]
        sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = updated_result
        total_confirmed_result = MagicMock()
        total_confirmed_result.count = confirmed_count
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = total_confirmed_result
        return sb

    @pytest.mark.asyncio
    async def test_confirm_sets_is_confirmed_true(self):
        sb = self._make_sb("done", 3)
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/offers/confirm",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confirmed"] == 3
        assert data["flyer_id"] == "flyer-1"

    @pytest.mark.asyncio
    async def test_confirm_idempotent_zero_count(self):
        sb = self._make_sb("done", 0)
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/offers/confirm",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )
        assert resp.status_code == 200
        assert resp.json()["confirmed"] == 0
