"""Unit tests for product fuzzy deduplication: normalize_for_comparison + _find_similar_product."""

from __future__ import annotations

import sys
import os
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders", "requests"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_settings = MagicMock()
_settings.llm_provider = "gemini"
_settings.google_api_key = "test-key"
_settings.gemini_model = "gemma-4-31b-it"
_settings.product_name_similarity_threshold = 0.85
_settings.product_brand_similarity_threshold = 0.90
_config_mod.settings = _settings
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

import pytest
from services.extraction.normalizer import normalize_for_comparison
from services.extraction.service import ExtractionService


# ---------------------------------------------------------------------------
# normalize_for_comparison
# ---------------------------------------------------------------------------

class TestNormalizeForComparison:
    def test_strips_accent(self):
        assert normalize_for_comparison("Pomì") == "pomi"

    def test_strips_multiple_diacritics(self):
        assert normalize_for_comparison("Caffè") == "caffe"

    def test_casefolds(self):
        assert normalize_for_comparison("Müller") == "muller"

    def test_plain_string_unchanged_except_case(self):
        assert normalize_for_comparison("Barilla") == "barilla"

    def test_empty_string(self):
        assert normalize_for_comparison("") == ""


# ---------------------------------------------------------------------------
# _find_similar_product
#
# Candidates are pre-filtered by brand (exact DB query) before being passed
# to _find_similar_product. The function performs name fuzzy-matching only.
# ---------------------------------------------------------------------------

@pytest.fixture()
def service() -> ExtractionService:
    return ExtractionService()


class TestFindSimilarProduct:

    # --- Name matches within same-brand candidates ---

    def test_matches_exact_name(self, service):
        existing = [{"id": "prod-1", "name": "Passata Di Pomodoro", "brand": "Pomì"}]
        incoming = {"name": "Passata Di Pomodoro", "brand": "Pomì"}
        assert service._find_similar_product(incoming, existing) == "prod-1"

    def test_matches_name_with_extra_specificity(self, service):
        existing = [{"id": "prod-2", "name": "Caffè Aromadicasa Miscela Forte", "brand": "Caffè Vergnano"}]
        incoming = {"name": "Caffè Aromadicasa Miscela Forte Macinatura Moka", "brand": "Caffè Vergnano"}
        assert service._find_similar_product(incoming, existing) == "prod-2"

    def test_matches_name_with_extra_specificity_reversed(self, service):
        existing = [{"id": "prod-2", "name": "Caffè Aromadicasa Miscela Forte Macinatura Moka", "brand": "Caffè Vergnano"}]
        incoming = {"name": "Caffè Aromadicasa Miscela Forte", "brand": "Caffè Vergnano"}
        assert service._find_similar_product(incoming, existing) == "prod-2"

    def test_matches_regardless_of_format(self, service):
        # Same product different format → still matches (format is an offer attribute now)
        existing = [{"id": "prod-7", "name": "Nutella", "brand": "Ferrero"}]
        incoming = {"name": "Nutella", "brand": "Ferrero"}
        assert service._find_similar_product(incoming, existing) == "prod-7"

    # --- No match on different name ---

    def test_no_match_completely_different_name(self, service):
        existing = [{"id": "prod-5", "name": "Latte Intero Fresco", "brand": "Granarolo"}]
        incoming = {"name": "Passata Di Pomodoro", "brand": "Pomì"}
        assert service._find_similar_product(incoming, existing) is None

    # --- Null brand candidates ---

    def test_both_brand_null_matches_on_name(self, service):
        existing = [{"id": "prod-6", "name": "Passata Di Pomodoro", "brand": None}]
        incoming = {"name": "Passata Di Pomodoro", "brand": None}
        assert service._find_similar_product(incoming, existing) == "prod-6"

    # --- Empty candidates ---

    def test_empty_candidates(self, service):
        incoming = {"name": "Passata Di Pomodoro", "brand": "Pomì"}
        assert service._find_similar_product(incoming, []) is None
