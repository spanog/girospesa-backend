"""Unit tests for draft-offers endpoints in api/routers/flyers.py."""

from __future__ import annotations

import sys
import os
import types
from unittest.mock import ANY, MagicMock, patch

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
_settings_obj.webhook_secret = "super-secret"
_config_mod.settings = _settings_obj  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()
sys.modules.pop("core.auth", None)
_normalizer_mod = types.ModuleType("services.extraction.normalizer")
_normalizer_mod.format_unit_price_label = lambda value, unit: None  # type: ignore[attr-defined]
_normalizer_mod.normalize_unit_price_measure = lambda value: value  # type: ignore[attr-defined]
sys.modules["services.extraction.normalizer"] = _normalizer_mod

from fastapi import FastAPI
import httpx
import pytest

import api.routers.flyers as _flyers_module
from api.routers.flyers import router
from tests.snapshot_utils import assert_matches_json_snapshot

sys.modules.pop("services.extraction.normalizer", None)

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


async def _post_multipart(url: str, dep_overrides: dict, files: dict) -> httpx.Response:
    test_app.dependency_overrides = dep_overrides
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, files=files)


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
        mock_service_module = types.ModuleType("services.extraction.service")
        mock_service_module.ExtractionService = mock_svc  # type: ignore[attr-defined]
        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch.dict(sys.modules, {"services.extraction.service": mock_service_module}),
        ):
            resp = await _post(
                "/flyers/flyer-1/extract",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )
        assert resp.status_code == 202
        assert resp.json()["status"] == "processing"
        update_payload = sb.table.return_value.update.call_args_list[-1][0][0]
        assert update_payload["status"] == "processing"
        assert update_payload["error_message"] is None

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
                "format": {
                    "tipo": "confezione_singola",
                    "peso_volume": 125,
                    "unita_misura": "g",
                },
                "format_label": "125 g",
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
    async def test_create_returns_201(self, request):
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
        assert_matches_json_snapshot(request, "draft_offer_create_response", data)

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
    async def test_create_ignores_offer_level_dates_and_uses_flyer_dates(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post(
                "/flyers/flyer-1/draft-offers",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json={
                    "name": "Mozzarella",
                    "price_offer": 1.99,
                    "valid_from": "2026-06-01",
                    "valid_to": "2026-06-30",
                },
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
    async def test_create_persists_subcategory_on_draft_offer(self):
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
        offer_insert = sb.table("offers").insert.call_args.args[0]
        assert offer_insert["product_id"] is None
        assert offer_insert["draft_name"] == "Mozzarella"
        assert offer_insert["draft_category"] == "alimentari-freschi"
        assert offer_insert["draft_subcategory"] == "Latticini e Formaggi"
        sb.table("products").upsert.assert_not_called()


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
                "draft_image_url": "https://storage.test/drafts/offer-1.png",
                "unit_price": "1,29 €/kg",
                "unit_price_value": 1.29,
                "unit_price_unit": "kg",
                "products": {
                    "id": "prod-1",
                    "name": "Pasta",
                    "brand": "Barilla",
                    "category": "dispensa",
                    "subcategory": "Primi Piatti e Preparati",
                    "format": {
                        "tipo": "confezione_singola",
                        "peso_volume": 500,
                        "unita_misura": "g",
                    },
                    "format_label": "500 g",
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
        assert data[0]["image_url"] == "https://storage.test/drafts/offer-1.png"


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
                "format": {
                    "tipo": "confezione_singola",
                    "peso_volume": 500,
                    "unita_misura": "g",
                },
                "format_label": "500 g",
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
    async def test_confirmed_offer_cannot_be_detached(self):
        sb = self._make_sb(is_confirmed=True)
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _patch_req(
                "/flyers/flyer-1/draft-offers/offer-1",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
                json={"detach_product": True},
            )
        assert resp.status_code == 409

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
            c.args[0].get("draft_subcategory") == "Snack Salati e Dolciumi"
            for c in update_calls
        )


class TestUploadDraftOfferImage:
    def _make_sb(
        self,
        *,
        product_id: str | None = None,
        is_confirmed: bool = False,
    ) -> MagicMock:
        sb = MagicMock()
        sb.storage.from_.return_value.upload.return_value = MagicMock()
        sb.storage.from_.return_value.get_public_url.return_value = (
            "https://storage.test/product-images/draft-offers/offer-1/prod.png"
        )

        flyer_result = MagicMock()
        flyer_result.data = {"id": "flyer-1", "supermarket_id": "sup-1"}
        offer_result = MagicMock()
        offer_result.data = {
            "id": "offer-1",
            "product_id": product_id,
            "flyer_id": "flyer-1",
            "is_confirmed": is_confirmed,
        }
        updated_result = MagicMock()
        updated_result.data = {
            "id": "offer-1",
            "flyer_id": "flyer-1",
            "product_id": None,
            "draft_name": "Pasta",
            "draft_brand": "Barilla",
            "draft_category": "dispensa",
            "draft_subcategory": "Primi Piatti e Preparati",
            "draft_image_url": "https://storage.test/product-images/draft-offers/offer-1/prod.png",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "price_offer": 1.99,
            "price_original": None,
            "discount_pct": None,
            "unit_price": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "offer_notes": None,
            "valid_from": None,
            "valid_to": None,
            "is_confirmed": False,
            "is_reviewed": False,
            "format": None,
            "format_key": "v1",
            "format_label": "500 g",
            "created_at": "2026-05-14T00:00:00Z",
            "products": None,
        }

        call_count = 0

        def select_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            chain = MagicMock()
            if call_count == 1:
                chain.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
            elif call_count == 2:
                chain.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = offer_result
            else:
                chain.eq.return_value.single.return_value.execute.return_value = updated_result
            return chain

        sb.table.return_value.select.side_effect = select_side_effect
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        return sb

    @pytest.mark.asyncio
    async def test_uploads_image_for_unbound_draft(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_multipart(
                "/flyers/flyer-1/draft-offers/offer-1/image",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                files={"file": ("prod.png", b"png", "image/png")},
            )

        assert resp.status_code == 200
        assert resp.json()["image_url"].endswith("/prod.png")
        assert any(
            call.args[0].get("draft_image_url") is not None
            for call in sb.table.return_value.update.call_args_list
        )

    @pytest.mark.asyncio
    async def test_rejects_unsupported_image_type(self):
        sb = self._make_sb()
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_multipart(
                "/flyers/flyer-1/draft-offers/offer-1/image",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                files={"file": ("prod.bmp", b"bmp", "image/bmp")},
            )

        assert resp.status_code == 415

    @pytest.mark.asyncio
    async def test_rejects_upload_for_bound_product(self):
        sb = self._make_sb(product_id="prod-1")
        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _post_multipart(
                "/flyers/flyer-1/draft-offers/offer-1/image",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                files={"file": ("prod.png", b"png", "image/png")},
            )

        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# confirm_offers
# ---------------------------------------------------------------------------

class TestConfirmOffers:
    def _make_sb(self, flyer_status: str = "done", confirmed_count: int = 3) -> MagicMock:
        sb = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {
            "id": "flyer-1",
            "supermarket_id": "sup-1",
            "status": flyer_status,
            "flyer_kind": "source",
            "user_id": "admin-1",
            "file_url": "https://storage.test/flyer.pdf",
            "file_type": "pdf",
            "file_name": "flyer.pdf",
            "valid_from": None,
            "valid_to": None,
            "pages_count": 1,
            "file_hash": "hash-1",
        }
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result

        updated_result = MagicMock()
        updated_result.data = [{"id": f"offer-{i}"} for i in range(confirmed_count)]
        sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = updated_result
        total_confirmed_result = MagicMock()
        total_confirmed_result.data = [
            {"id": f"offer-{i}", "product_id": f"prod-{i}"}
            for i in range(confirmed_count)
        ]
        total_confirmed_result.count = confirmed_count
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = total_confirmed_result
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = total_confirmed_result
        return sb

    @pytest.mark.asyncio
    async def test_confirm_sets_is_confirmed_true(self):
        sb = self._make_sb("done", 3)
        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch("api.routers.flyers._flyer_targets", return_value=[
                {"supermarket_id": "sup-1", "supermarket_name": "Coop"},
                {"supermarket_id": "sup-2", "supermarket_name": "Conad"},
            ]),
            patch(
                "api.routers.flyers._published_target_flyers",
                side_effect=[
                    {},
                    {
                        "sup-1": {"flyer_id": "published-1", "supermarket_name": "Coop"},
                        "sup-2": {"flyer_id": "published-2", "supermarket_name": "Conad"},
                    },
                ],
            ),
            patch("api.routers.flyers._sync_published_clones_for_source_offer") as sync_mock,
            patch("api.routers.flyers.notify_public_flyer_published") as notify_mock,
        ):
            resp = await _post(
                "/flyers/flyer-1/offers/confirm",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confirmed"] == 3
        assert data["flyer_id"] == "flyer-1"
        assert len(data["published_flyers"]) == 2
        notify_mock.assert_any_call(
            sb,
            flyer_id=ANY,
            supermarket_id="sup-1",
            supermarket_name="Coop",
            products_count=3,
        )
        notify_mock.assert_any_call(
            sb,
            flyer_id=ANY,
            supermarket_id="sup-2",
            supermarket_name="Conad",
            products_count=3,
        )
        sync_mock.assert_called()
        assert any(
            call.args[0] == {"is_confirmed": True, "offer_kind": "source_master"}
            for call in sb.table.return_value.update.call_args_list
        )

    @pytest.mark.asyncio
    async def test_confirm_idempotent_zero_count(self):
        sb = self._make_sb("done", 0)
        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch("api.routers.flyers._flyer_targets", return_value=[{"supermarket_id": "sup-1", "supermarket_name": "Coop"}]),
            patch("api.routers.flyers.notify_public_flyer_published") as notify_mock,
        ):
            resp = await _post(
                "/flyers/flyer-1/offers/confirm",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )
        assert resp.status_code == 200
        assert resp.json()["confirmed"] == 0
        notify_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_does_not_notify_when_flyer_already_public(self):
        sb = self._make_sb("done", 2)
        flyer_result = MagicMock()
        flyer_result.data = {
            "id": "flyer-1",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "status": "done",
            "is_public": True,
            "flyer_kind": "source",
            "user_id": "admin-1",
            "file_url": "https://storage.test/flyer.pdf",
            "file_type": "pdf",
            "file_name": "flyer.pdf",
            "pages_count": 1,
            "file_hash": "hash-1",
        }
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
            flyer_result
        )

        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch("api.routers.flyers._flyer_targets", return_value=[{"supermarket_id": "sup-1", "supermarket_name": "Coop"}]),
            patch(
                "api.routers.flyers._published_target_flyers",
                side_effect=[
                    {"sup-1": {"flyer_id": "published-1", "supermarket_name": "Coop"}},
                    {"sup-1": {"flyer_id": "published-1", "supermarket_name": "Coop"}},
                ],
            ),
            patch("api.routers.flyers._sync_published_clones_for_source_offer"),
            patch("api.routers.flyers.notify_public_flyer_published") as notify_mock,
        ):
            resp = await _post(
                "/flyers/flyer-1/offers/confirm",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )

        assert resp.status_code == 200
        notify_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_dispatches_favorite_notifications_without_webhook_secret(self):
        sb = self._make_sb("done", 1)
        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch("api.routers.flyers._flyer_targets", return_value=[
                {"supermarket_id": "sup-1", "supermarket_name": "Coop"},
            ]),
            patch(
                "api.routers.flyers._published_target_flyers",
                side_effect=[
                    {},
                    {"sup-1": {"flyer_id": "published-1", "supermarket_name": "Coop"}},
                ],
            ),
            patch("api.routers.flyers.notify_public_flyer_published"),
            patch("api.routers.flyers.notify_favorite_offer_published") as favorite_mock,
            patch.object(_flyers_module.settings, "webhook_secret", ""),
        ):
            resp = await _post(
                "/flyers/flyer-1/offers/confirm",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )

        assert resp.status_code == 200
        favorite_mock.assert_called_once()
        clone = favorite_mock.call_args.args[1]
        assert clone["product_id"] == "prod-0"
        assert clone["flyer_id"] == "published-1"
        assert clone["supermarket_id"] == "sup-1"
        assert clone["offer_kind"] == "published_target"

    @pytest.mark.asyncio
    async def test_confirm_passes_draft_image_to_new_product(self):
        sb = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {
            "id": "flyer-1",
            "supermarket_id": "sup-1",
            "status": "done",
            "flyer_kind": "source",
            "user_id": "admin-1",
            "file_url": "https://storage.test/flyer.pdf",
            "file_type": "pdf",
            "file_name": "flyer.pdf",
            "pages_count": 1,
            "file_hash": "hash-1",
        }
        drafts_result = MagicMock()
        drafts_result.data = [
            {
                "id": "offer-1",
                "product_id": None,
                "draft_name": "Pasta",
                "draft_brand": "Barilla",
                "draft_category": "dispensa",
                "draft_subcategory": "Primi Piatti e Preparati",
                "draft_image_url": "https://storage.test/product-images/draft-offers/offer-1/prod.png",
            }
        ]
        total_confirmed_result = MagicMock()
        total_confirmed_result.data = [{"id": "offer-1"}]
        total_confirmed_result.count = 1

        call_count = 0

        def select_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            chain = MagicMock()
            if call_count == 1:
                chain.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
            elif call_count == 2:
                chain.eq.return_value.eq.return_value.execute.return_value = drafts_result
            else:
                chain.eq.return_value.eq.return_value.execute.return_value = total_confirmed_result
            return chain

        sb.table.return_value.select.side_effect = select_side_effect
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch("api.routers.flyers._flyer_targets", return_value=[{"supermarket_id": "sup-1", "supermarket_name": "Coop"}]),
            patch(
                "api.routers.flyers._published_target_flyers",
                side_effect=[
                    {},
                    {"sup-1": {"flyer_id": "published-1", "supermarket_name": "Coop"}},
                ],
            ),
            patch("api.routers.flyers._sync_published_clones_for_source_offer"),
            patch("api.routers.flyers.upsert_product", return_value="prod-new") as mock_upsert,
        ):
            resp = await _post(
                "/flyers/flyer-1/offers/confirm",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE, _DEP_USER_ID: lambda: "admin-1"},
            )

        assert resp.status_code == 200
        assert mock_upsert.call_args.args[1]["image_url"] == (
            "https://storage.test/product-images/draft-offers/offer-1/prod.png"
        )

    def test_sync_published_clones_updates_existing_rows_without_inserting_duplicates(self):
        sb = MagicMock()
        clone_result = MagicMock()
        clone_result.data = [{"id": "clone-1", "supermarket_id": "sup-1", "source_offer_id": "offer-1"}]
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = clone_result

        _flyers_module._sync_published_clones_for_source_offer(
            sb,
            source_offer={
                "id": "offer-1",
                "product_id": "prod-1",
                "draft_name": "Pasta",
                "draft_brand": "Barilla",
                "draft_category": "dispensa",
                "draft_subcategory": None,
                "draft_product_key": "pasta|barilla",
                "draft_image_url": None,
                "price_original": 2.99,
                "price_offer": 1.99,
                "discount_pct": 33,
                "unit_price": None,
                "unit_price_value": None,
                "unit_price_unit": None,
                "offer_type": None,
                "offer_notes": None,
                "valid_from": "2026-06-01",
                "valid_to": "2026-06-10",
                "is_active": True,
                "raw_text": None,
                "confidence_score": None,
                "format": None,
                "format_key": "fmt-1",
                "format_label": "500 g",
                "is_reviewed": False,
            },
            target_flyers={"sup-1": {"flyer_id": "flyer-target-1", "supermarket_name": "Coop"}},
        )

        sb.table.return_value.update.assert_called_once()
        sb.table.return_value.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_patch_confirmed_source_offer_syncs_published_clones(self):
        sb = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {
            "id": "flyer-1",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "flyer_kind": "source",
        }
        offer_result = MagicMock()
        offer_result.data = {
            "id": "offer-1",
            "product_id": "prod-1",
            "flyer_id": "flyer-1",
            "is_confirmed": True,
        }
        updated_offer = {
            "id": "offer-1",
            "flyer_id": "flyer-1",
            "product_id": "prod-1",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "draft_name": "Pasta Integrale",
            "draft_brand": "Barilla",
            "draft_category": "dispensa",
            "draft_subcategory": None,
            "draft_product_key": "pasta integrale|barilla",
            "draft_image_url": None,
            "price_offer": 1.99,
            "price_original": None,
            "discount_pct": None,
            "unit_price": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "offer_notes": None,
            "valid_from": None,
            "valid_to": None,
            "is_confirmed": True,
            "offer_kind": "source_master",
            "is_reviewed": False,
            "format": None,
            "format_key": None,
            "format_label": "",
            "created_at": "2026-05-14T00:00:00Z",
            "products": {
                "id": "prod-1",
                "name": "Pasta Integrale",
                "brand": "Barilla",
                "category": "dispensa",
                "subcategory": None,
                "image_url": None,
            },
        }
        final_result = MagicMock()
        final_result.data = updated_offer

        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = offer_result
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = final_result
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        with (
            patch("api.routers.flyers.get_supabase", return_value=sb),
            patch(
                "api.routers.flyers._published_target_flyers",
                return_value={"sup-1": {"flyer_id": "published-1", "supermarket_name": "Coop"}},
            ),
            patch("api.routers.flyers._sync_published_clones_for_source_offer") as sync_mock,
        ):
            resp = await _patch_req(
                "/flyers/flyer-1/draft-offers/offer-1",
                {_DEP_PROFILE: lambda: ADMIN_PROFILE},
                json={"name": "Pasta Integrale"},
            )

        assert resp.status_code == 200
        sync_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_confirmed_source_offer_removes_published_clones(self):
        sb = MagicMock()
        flyer_result = MagicMock()
        flyer_result.data = {
            "id": "flyer-1",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "flyer_kind": "source",
        }
        offer_result = MagicMock()
        offer_result.data = {
            "id": "offer-1",
            "flyer_id": "flyer-1",
            "is_confirmed": True,
        }
        sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = offer_result
        sb.table.return_value.delete.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        with patch("api.routers.flyers.get_supabase", return_value=sb):
            resp = await _flyers_module.delete_draft_offer(
                "flyer-1",
                "offer-1",
                profile=ADMIN_PROFILE,
            )

        assert resp is None
        eq_calls = sb.table.return_value.delete.return_value.eq.call_args_list
        assert any(call.args == ("source_offer_id", "offer-1") for call in eq_calls)
        assert any(call.args == ("id", "offer-1") for call in eq_calls)


# ---------------------------------------------------------------------------
# is_reviewed field — GET and PATCH
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_draft_offers_includes_is_reviewed():
    """is_reviewed field is present in GET /draft-offers response."""
    sb = MagicMock()
    flyer_data = {"id": "flyer-1", "supermarket_id": "sup-1", "status": "done"}
    flyer_result = MagicMock()
    flyer_result.data = flyer_data
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result

    offer_row = {
        "id": "offer-1",
        "flyer_id": "flyer-1",
        "product_id": "prod-1",
        "supermarket_id": "sup-1",
        "supermarket_name": "Test",
        "price_offer": 1.99,
        "price_original": None,
        "discount_pct": None,
        "unit_price": None,
        "unit_price_value": None,
        "unit_price_unit": None,
        "offer_notes": None,
        "valid_from": None,
        "valid_to": None,
        "is_confirmed": False,
        "is_reviewed": False,
        "format": None,
        "format_key": None,
        "format_label": "",
        "created_at": "2026-05-14T00:00:00Z",
        "products": {
            "id": "prod-1",
            "name": "Latte",
            "brand": None,
            "category": None,
            "subcategory": None,
            "image_url": None,
        },
    }
    offers_result = MagicMock()
    offers_result.data = [offer_row]

    call_count = 0

    def select_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        chain = MagicMock()
        if call_count == 1:
            chain.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
        else:
            chain.eq.return_value.eq.return_value.execute.return_value = offers_result
        return chain

    sb.table.return_value.select.side_effect = select_side_effect

    with patch("api.routers.flyers.get_supabase", return_value=sb):
        resp = await _get(
            "/flyers/flyer-1/draft-offers",
            {_DEP_PROFILE: lambda: ADMIN_PROFILE},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "is_reviewed" in data[0]
    assert data[0]["is_reviewed"] is False


@pytest.mark.anyio
async def test_list_draft_offers_exposes_existing_product_binding():
    """Draft response tells reviewers when offer is bound to catalog product."""
    sb = MagicMock()
    flyer_result = MagicMock()
    flyer_result.data = {"id": "flyer-1", "supermarket_id": "sup-1"}
    offers_result = MagicMock()
    offers_result.data = [
        {
            "id": "offer-1",
            "flyer_id": "flyer-1",
            "product_id": "prod-1",
            "draft_name": "Latte alta digeribilita",
            "draft_brand": "Berna",
            "draft_category": "freschi",
            "draft_subcategory": "Latte",
            "supermarket_id": "sup-1",
            "supermarket_name": "Coop",
            "price_offer": 1.49,
            "price_original": None,
            "discount_pct": None,
            "unit_price": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "offer_notes": None,
            "valid_from": None,
            "valid_to": None,
            "is_confirmed": False,
            "is_reviewed": False,
            "format": None,
            "format_key": "v1:1l",
            "format_label": "1 L",
            "created_at": "2026-05-14T00:00:00Z",
            "products": {
                "id": "prod-1",
                "name": "Latte Alta Digeribilita",
                "brand": "Berna",
                "category": "freschi",
                "subcategory": "Latte",
                "image_url": None,
            },
        }
    ]

    def select_side_effect(*args, **kwargs):
        chain = MagicMock()
        if "supermarket_id" in args[0]:
            chain.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
        else:
            chain.eq.return_value.eq.return_value.execute.return_value = offers_result
        return chain

    sb.table.return_value.select.side_effect = select_side_effect

    with patch("api.routers.flyers.get_supabase", return_value=sb):
        resp = await _get("/flyers/flyer-1/draft-offers", {_DEP_PROFILE: lambda: ADMIN_PROFILE})

    assert resp.status_code == 200
    draft = resp.json()[0]
    assert draft["name"] == "Latte alta digeribilita"
    assert draft["linked_product"]["id"] == "prod-1"
    assert draft["linked_product"]["name"] == "Latte Alta Digeribilita"
    assert draft["binding_status"] == "existing"


@pytest.mark.anyio
async def test_patch_draft_offer_sets_is_reviewed():
    """PATCH /draft-offers/{id} with is_reviewed=true persists and returns the flag."""
    sb = MagicMock()
    flyer_data = {"id": "flyer-1", "supermarket_id": "sup-1", "supermarket_name": "Test"}
    flyer_result = MagicMock()
    flyer_result.data = flyer_data
    offer_data = {
        "id": "offer-1",
        "product_id": "prod-1",
        "flyer_id": "flyer-1",
        "is_confirmed": False,
    }
    offer_result = MagicMock()
    offer_result.data = offer_data

    updated_offer = {
        "id": "offer-1",
        "flyer_id": "flyer-1",
        "product_id": "prod-1",
        "supermarket_id": "sup-1",
        "supermarket_name": "Test",
        "price_offer": 1.99,
        "price_original": None,
        "discount_pct": None,
        "unit_price": None,
        "unit_price_value": None,
        "unit_price_unit": None,
        "offer_notes": None,
        "valid_from": None,
        "valid_to": None,
        "is_confirmed": False,
        "is_reviewed": True,
        "format": None,
        "format_key": None,
        "format_label": "",
        "created_at": "2026-05-14T00:00:00Z",
        "products": {
            "id": "prod-1",
            "name": "Latte",
            "brand": None,
            "category": None,
            "subcategory": None,
            "image_url": None,
        },
    }
    final_result = MagicMock()
    final_result.data = updated_offer

    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = offer_result
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = final_result

    with patch("api.routers.flyers.get_supabase", return_value=sb):
        resp = await _patch_req(
            "/flyers/flyer-1/draft-offers/offer-1",
            {_DEP_PROFILE: lambda: ADMIN_PROFILE},
            json={"is_reviewed": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_reviewed"] is True


@pytest.mark.anyio
async def test_patch_draft_offer_detaches_existing_product_without_creating_one():
    """Detaching removes product_id only; product creation is deferred to confirmation."""
    sb = MagicMock()
    flyer_result = MagicMock(data={"id": "flyer-1", "supermarket_id": "sup-1"})
    offer_result = MagicMock(data={"id": "offer-1", "product_id": "prod-1", "flyer_id": "flyer-1", "is_confirmed": False})
    updated_result = MagicMock(data={
        "id": "offer-1",
        "flyer_id": "flyer-1",
        "product_id": None,
        "draft_name": "Latte",
        "draft_brand": "Berna",
        "draft_category": "freschi",
        "draft_subcategory": None,
        "supermarket_id": "sup-1",
        "supermarket_name": "Test",
        "price_offer": 1.99,
        "price_original": None,
        "discount_pct": None,
        "unit_price": None,
        "unit_price_value": None,
        "unit_price_unit": None,
        "offer_notes": None,
        "valid_from": None,
        "valid_to": None,
        "is_confirmed": False,
        "is_reviewed": False,
        "format": None,
        "format_key": "",
        "format_label": "",
        "created_at": "2026-05-14T00:00:00Z",
        "products": None,
    })

    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = flyer_result
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = offer_result
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = updated_result

    with patch("api.routers.flyers.get_supabase", return_value=sb):
        resp = await _patch_req(
            "/flyers/flyer-1/draft-offers/offer-1",
            {_DEP_PROFILE: lambda: ADMIN_PROFILE},
            json={"detach_product": True},
        )

    assert resp.status_code == 200
    assert resp.json()["product_id"] is None
    assert resp.json()["binding_status"] == "new_on_confirm"
    assert any(call.args[0]["product_id"] is None for call in sb.table.return_value.update.call_args_list)
    assert not any(call.args[0].get("name") == "Latte" for call in sb.table.return_value.upsert.call_args_list)
