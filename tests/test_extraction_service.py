"""Unit tests for services/extraction/service.py — ExtractionService."""

from __future__ import annotations

import sys
import os
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub infrastructure modules
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders", "requests"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_settings = MagicMock()
_settings.llm_provider = "gemini"
_settings.google_api_key = "test-key"
_settings.gemini_model = "gemma-4-31b-it"
_config_mod.settings = _settings
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

import pytest


def _make_sb(
    flyer_data: dict | None = None,
    upsert_data: list | None = None,
    select_fallback_data: list | None = None,
) -> MagicMock:
    """Build a Supabase mock that covers the full extraction pipeline."""
    sb = MagicMock()

    # _fetch_flyer — .select().eq().single().execute()
    flyer_result = MagicMock()
    flyer_result.data = flyer_data or {
        "id": "flyer-1",
        "file_url": "https://example.com/flyer.jpg",
        "file_name": "flyer.jpg",
        "supermarket_id": "sup-1",
        "supermarket_name": "Test Super",
        "valid_from": None,
        "valid_to": "2026-05-01",
    }

    # _upsert_product — .upsert().execute()
    upsert_result = MagicMock()
    upsert_result.data = upsert_data if upsert_data is not None else [{"id": "prod-uuid"}]

    # offers.insert().execute()
    insert_result = MagicMock()
    insert_result.data = [{"id": "offer-uuid"}]

    # flyers.update().eq().execute()
    update_result = MagicMock()
    update_result.data = []

    # Build chained mock
    table_mock = MagicMock()
    sb.table.return_value = table_mock

    # .select(...).eq(...).single().execute() → flyer
    table_mock.select.return_value.eq.return_value.single.return_value.execute.return_value = flyer_result
    # .upsert(...).execute() → product
    table_mock.upsert.return_value.execute.return_value = upsert_result
    # .insert(...).execute() → offers
    table_mock.insert.return_value.execute.return_value = insert_result
    # .update(...).eq(...).execute() → flyer status update
    table_mock.update.return_value.eq.return_value.execute.return_value = update_result

    return sb


_EXTRACTED_PRODUCTS = [
    {
        "name": "Pasta Barilla",
        "brand": "Barilla",
        "category": "dispensa",
        "format": "500g",
        "price_offer": 1.29,
        "price_original": 1.79,
        "valid_from": "2026-04-21",
        "valid_to": "2026-04-27",
    }
]

_EXTRACTED_PRODUCTS_V2 = [
    {
        "name": "Tonno all'olio di oliva",
        "brand": "Rio Mare",
        "category_main": "Dispensa",
        "category_sub": "Conserve Ittiche e di Carne",
        "format": "2x80g",
        "price_current": 4.99,
        "price_original": 6.79,
        "discount_percentage": 26,
        "price_per_unit": 31.19,
        "price_per_unit_measure": "kg",
        "valid_from": "2026-04-21",
        "valid_to": "2026-04-27",
    }
]


class TestExtractionServiceRunSetsOfferAsUnconfirmed:
    """is_confirmed must be False on all inserted offers."""

    def test_run_inserts_draft_offers_not_confirmed(self):
        sb = _make_sb()
        mock_provider = MagicMock()
        mock_provider.extract_products.return_value = (_EXTRACTED_PRODUCTS, [])

        with (
            patch("services.extraction.service.requests.get") as mock_get,
            patch("services.extraction.service.count_pdf_pages", return_value=1),
        ):
            mock_get.return_value.content = b"%PDF-fake"
            mock_get.return_value.raise_for_status = MagicMock()

            from services.extraction.service import ExtractionService
            svc = ExtractionService(provider=mock_provider, supabase_factory=lambda: sb)
            svc.run("flyer-1")

        # Find list-based insert call for offers, not extraction_log row inserts.
        all_insert_calls = sb.table.return_value.insert.call_args_list
        offer_inserts = [
            c for c in all_insert_calls
            if isinstance(c[0][0], list)
        ]
        assert len(offer_inserts) >= 1, "Expected at least one batch offer insert"
        offer_rows = offer_inserts[0][0][0]
        assert isinstance(offer_rows, list)
        for row in offer_rows:
            assert row["is_confirmed"] is False


class TestExtractionServiceStatusTransitions:
    """Flyer status set to 'done' on success."""

    def test_run_sets_flyer_status_done(self):
        sb = _make_sb()
        mock_provider = MagicMock()
        mock_provider.extract_products.return_value = (_EXTRACTED_PRODUCTS, [])

        with (
            patch("services.extraction.service.requests.get") as mock_get,
            patch("services.extraction.service.count_pdf_pages", return_value=1),
        ):
            mock_get.return_value.content = b"%PDF-fake"
            mock_get.return_value.raise_for_status = MagicMock()

            from services.extraction.service import ExtractionService
            svc = ExtractionService(provider=mock_provider, supabase_factory=lambda: sb)
            svc.run("flyer-1")

        update_calls = sb.table.return_value.update.call_args_list
        done_calls = [c for c in update_calls if c[0][0].get("status") == "done"]
        assert len(done_calls) >= 1

    def test_run_persists_structured_unit_price_fields(self):
        sb = _make_sb()
        mock_provider = MagicMock()
        mock_provider.extract_products.return_value = (_EXTRACTED_PRODUCTS_V2, [])

        with (
            patch("services.extraction.service.requests.get") as mock_get,
            patch("services.extraction.service.count_pdf_pages", return_value=1),
        ):
            mock_get.return_value.content = b"%PDF-fake"
            mock_get.return_value.raise_for_status = MagicMock()

            from services.extraction.service import ExtractionService
            svc = ExtractionService(provider=mock_provider, supabase_factory=lambda: sb)
            svc.run("flyer-1")

        all_insert_calls = sb.table.return_value.insert.call_args_list
        offer_inserts = [c for c in all_insert_calls if isinstance(c[0][0], list)]
        offer_rows = offer_inserts[0][0][0]
        assert offer_rows[0]["unit_price_value"] == pytest.approx(31.19)
        assert offer_rows[0]["unit_price_unit"] == "kg"
        assert offer_rows[0]["unit_price"] == "31,19 €/kg"


class TestExtractionServiceErrorPath:
    """On provider failure, flyer status must be set to 'error'."""

    def test_run_provider_failure_sets_error(self):
        sb = _make_sb()
        mock_provider = MagicMock()
        mock_provider.extract_products.side_effect = RuntimeError("Gemini timeout")

        with (
            patch("services.extraction.service.requests.get") as mock_get,
            patch("services.extraction.service.count_pdf_pages", return_value=1),
        ):
            mock_get.return_value.content = b"%PDF-fake"
            mock_get.return_value.raise_for_status = MagicMock()

            from services.extraction.service import ExtractionService
            svc = ExtractionService(provider=mock_provider, supabase_factory=lambda: sb)
            svc.run("flyer-1")  # must not raise

        update_calls = sb.table.return_value.update.call_args_list
        error_calls = [c for c in update_calls if c[0][0].get("status") == "error"]
        assert len(error_calls) >= 1


class TestUpsertProductFallback:
    """_upsert_product falls back to SELECT when upsert returns empty data."""

    def test_upsert_product_conflict_fallback(self):
        sb = MagicMock()
        # upsert returns empty → triggers SELECT fallback
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
        # For brand/format present: chain is .select().eq(name).eq(brand).eq(format).limit().execute()
        existing = MagicMock()
        existing.data = [{"id": "existing-prod-uuid"}]
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = existing

        with patch("services.extraction.service.get_provider", return_value=MagicMock()):
            from services.extraction.service import ExtractionService
            svc = ExtractionService()
            product_id = svc._upsert_product(sb, {"name": "Pasta", "brand": "Barilla", "format": "500g"})

        assert product_id == "existing-prod-uuid"

    def test_upsert_product_not_found_raises(self):
        sb = MagicMock()
        # upsert returns empty
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
        # For None brand/format: chain is .select().eq(name).is_(brand).is_(format).limit().execute()
        empty = MagicMock()
        empty.data = []
        sb.table.return_value.select.return_value.eq.return_value.is_.return_value.is_.return_value.limit.return_value.execute.return_value = empty

        with patch("services.extraction.service.get_provider", return_value=MagicMock()):
            from services.extraction.service import ExtractionService
            svc = ExtractionService()
            with pytest.raises(ValueError, match="Product not found after upsert"):
                svc._upsert_product(sb, {"name": "Pasta", "brand": None, "format": None})


class TestExtractionServiceSubcategoryPersisted:
    """Subcategory from LLM response must reach the product upsert."""

    def test_subcategory_written_to_upsert(self):
        sb = _make_sb()
        mock_provider = MagicMock()
        mock_provider.extract_products.return_value = (_EXTRACTED_PRODUCTS_V2, [])

        with (
            patch("services.extraction.service.requests.get") as mock_get,
            patch("services.extraction.service.count_pdf_pages", return_value=1),
        ):
            mock_get.return_value.content = b"%PDF-fake"
            mock_get.return_value.raise_for_status = MagicMock()

            from services.extraction.service import ExtractionService
            svc = ExtractionService(provider=mock_provider, supabase_factory=lambda: sb)
            svc.run("flyer-1")

        upsert_calls = sb.table.return_value.upsert.call_args_list
        assert upsert_calls, "Expected at least one product upsert"
        product_row = upsert_calls[0][0][0]
        assert product_row.get("subcategory") == "Conserve Ittiche e di Carne"
