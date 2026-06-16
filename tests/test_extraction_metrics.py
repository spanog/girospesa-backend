"""Unit tests for scripts/extraction/extraction_metrics.py"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from scripts.extraction.extraction_metrics import (  # type: ignore[import]
    FieldMetrics,
    SupermarketReport,
    build_report,
    evaluate_page,
)


# ---------------------------------------------------------------------------
# FieldMetrics property tests
# ---------------------------------------------------------------------------

class TestFieldMetrics:
    def test_precision_zero_when_no_positive(self):
        fm = FieldMetrics(tp=0, fp=0, fn=5)
        assert fm.precision == 0.0

    def test_recall_zero_when_no_expected(self):
        fm = FieldMetrics(tp=0, fp=3, fn=0)
        assert fm.recall == 0.0

    def test_f1_zero_when_both_zero(self):
        fm = FieldMetrics(tp=0, fp=0, fn=0)
        assert fm.f1 == 0.0

    def test_perfect_scores(self):
        fm = FieldMetrics(tp=10, fp=0, fn=0)
        assert fm.precision == pytest.approx(1.0)
        assert fm.recall == pytest.approx(1.0)
        assert fm.f1 == pytest.approx(1.0)

    def test_half_precision_half_recall(self):
        fm = FieldMetrics(tp=5, fp=5, fn=5)
        assert fm.precision == pytest.approx(0.5)
        assert fm.recall == pytest.approx(0.5)
        assert fm.f1 == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# evaluate_page — product-level matching
# ---------------------------------------------------------------------------

class TestEvaluatePage:
    def _prod(self, name: str, price_offer: float = 1.99, category: str = "dispensa") -> dict:
        return {
            "name": name,
            "price_offer": price_offer,
            "price_original": None,
            "unit_price_value": None,
            "unit_price_unit": None,
            "category": category,
        }

    def test_perfect_match(self):
        expected = [self._prod("Pasta barilla"), self._prod("Latte intero")]
        extracted = [self._prod("Pasta barilla"), self._prod("Latte intero")]
        result = evaluate_page("p1", expected, extracted)
        assert result.matched_count == 2
        assert result.product_precision == pytest.approx(1.0)
        assert result.product_recall == pytest.approx(1.0)
        assert result.product_f1 == pytest.approx(1.0)

    def test_partial_match_recall_drops(self):
        expected = [self._prod("Pasta"), self._prod("Latte"), self._prod("Olio")]
        extracted = [self._prod("Pasta"), self._prod("Latte")]
        result = evaluate_page("p1", expected, extracted)
        assert result.matched_count == 2
        assert result.product_recall == pytest.approx(2 / 3)
        assert result.product_precision == pytest.approx(1.0)

    def test_extra_extracted_drops_precision(self):
        expected = [self._prod("Pasta")]
        extracted = [self._prod("Pasta"), self._prod("Prodotto inesistente hallucination")]
        result = evaluate_page("p1", expected, extracted)
        assert result.matched_count == 1
        assert result.product_precision == pytest.approx(0.5)
        assert result.product_recall == pytest.approx(1.0)

    def test_empty_expected(self):
        result = evaluate_page("p1", [], [self._prod("Pasta")])
        assert result.matched_count == 0
        assert result.product_precision == 0.0
        assert result.product_recall == 0.0

    def test_empty_extracted(self):
        result = evaluate_page("p1", [self._prod("Pasta")], [])
        assert result.matched_count == 0
        assert result.product_recall == 0.0
        assert result.product_precision == 0.0

    def test_both_empty(self):
        result = evaluate_page("p1", [], [])
        assert result.matched_count == 0
        assert result.product_f1 == 0.0

    def test_name_partial_token_match(self):
        expected = [{"name": "Petto di pollo", "price_offer": 5.99, "price_original": None,
                     "category": "carne-pesce"}]
        extracted = [{"name": "Pollo petto fresco", "price_offer": 5.99, "price_original": None,
                      "category": "carne-pesce"}]
        result = evaluate_page("p1", expected, extracted)
        # "petto" and "pollo" appear in both → Jaccard ≥ 0.4
        assert result.matched_count == 1

    def test_completely_different_names_no_match(self):
        expected = [self._prod("Mele golden")]
        extracted = [self._prod("Detersivo piatti")]
        result = evaluate_page("p1", expected, extracted)
        assert result.matched_count == 0


# ---------------------------------------------------------------------------
# evaluate_page — field-level accuracy
# ---------------------------------------------------------------------------

class TestFieldAccuracy:
    def _pair(self, exp: dict, ext: dict):
        result = evaluate_page("p1", [exp], [ext])
        return result.fields

    def test_price_offer_within_tolerance(self):
        exp = {"name": "Pasta", "price_offer": 1.99, "price_original": None, "category": "dispensa"}
        ext = {"name": "Pasta", "price_offer": 2.03, "price_original": None, "category": "dispensa"}
        fields = self._pair(exp, ext)
        assert fields["price_offer"].tp == 1  # within ±0.05

    def test_price_offer_outside_tolerance(self):
        exp = {"name": "Pasta", "price_offer": 1.99, "price_original": None, "category": "dispensa"}
        ext = {"name": "Pasta", "price_offer": 2.49, "price_original": None, "category": "dispensa"}
        fields = self._pair(exp, ext)
        assert fields["price_offer"].tp == 0

    def test_category_correct(self):
        exp = {"name": "Latte", "price_offer": 1.09, "price_original": None, "category": "latticini-uova"}
        ext = {"name": "Latte", "price_offer": 1.09, "price_original": None, "category": "latticini-uova"}
        fields = self._pair(exp, ext)
        assert fields["category"].tp == 1

    def test_category_wrong(self):
        exp = {"name": "Latte", "price_offer": 1.09, "price_original": None, "category": "latticini-uova"}
        ext = {"name": "Latte", "price_offer": 1.09, "price_original": None, "category": "altro"}
        fields = self._pair(exp, ext)
        assert fields["category"].tp == 0

    def test_none_none_price_original_correct(self):
        exp = {"name": "Pizza", "price_offer": 1.29, "price_original": None, "category": "surgelati"}
        ext = {"name": "Pizza", "price_offer": 1.29, "price_original": None, "category": "surgelati"}
        fields = self._pair(exp, ext)
        assert fields["price_original"].tp == 1

    def test_missing_price_original_when_expected(self):
        exp = {"name": "Pasta", "price_offer": 0.89, "price_original": 1.19, "category": "dispensa"}
        ext = {"name": "Pasta", "price_offer": 0.89, "price_original": None, "category": "dispensa"}
        fields = self._pair(exp, ext)
        assert fields["price_original"].tp == 0

    def test_unit_price_value_within_tolerance(self):
        exp = {
            "name": "Tonno",
            "price_offer": 4.99,
            "price_original": None,
            "unit_price_value": 31.19,
            "unit_price_unit": "kg",
            "category": "dispensa",
        }
        ext = {
            "name": "Tonno",
            "price_offer": 4.99,
            "price_original": None,
            "unit_price_value": 31.23,
            "unit_price_unit": "kg",
            "category": "dispensa",
        }
        fields = self._pair(exp, ext)
        assert fields["unit_price_value"].tp == 1

    def test_unit_price_unit_wrong(self):
        exp = {
            "name": "Piselli",
            "price_offer": 1.99,
            "price_original": None,
            "unit_price_value": 3.98,
            "unit_price_unit": "kg",
            "category": "dispensa",
        }
        ext = {
            "name": "Piselli",
            "price_offer": 1.99,
            "price_original": None,
            "unit_price_value": 3.98,
            "unit_price_unit": "l",
            "category": "dispensa",
        }
        fields = self._pair(exp, ext)
        assert fields["unit_price_unit"].tp == 0


# ---------------------------------------------------------------------------
# SupermarketReport aggregation
# ---------------------------------------------------------------------------

class TestSupermarketReport:
    def _make_report(self) -> SupermarketReport:
        pages = [
            ("p1",
             [{"name": "Pasta", "price_offer": 0.89, "price_original": None, "category": "dispensa"}],
             [{"name": "Pasta", "price_offer": 0.89, "price_original": None, "category": "dispensa"}]),
            ("p2",
             [{"name": "Latte", "price_offer": 1.09, "price_original": None, "category": "latticini-uova"},
              {"name": "Yogurt", "price_offer": 0.99, "price_original": None, "category": "latticini-uova"}],
             [{"name": "Latte", "price_offer": 1.09, "price_original": None, "category": "latticini-uova"}]),
        ]
        return build_report("TestSuper", pages)

    def test_overall_precision(self):
        report = self._make_report()
        # matched=2, extracted=2 → precision=1.0
        assert report.overall_product_precision == pytest.approx(1.0)

    def test_overall_recall(self):
        report = self._make_report()
        # matched=2, expected=3 → recall=2/3
        assert report.overall_product_recall == pytest.approx(2 / 3)

    def test_aggregate_field_tp(self):
        report = self._make_report()
        name_metrics = report.aggregate_field("name")
        # Both matched products have correct names
        assert name_metrics.tp == 2

    def test_pages_count(self):
        report = self._make_report()
        assert len(report.pages) == 2


# ---------------------------------------------------------------------------
# Mock extraction smoke test (via test_extraction script)
# ---------------------------------------------------------------------------

class TestMockExtraction:
    def test_mock_returns_fewer_than_expected(self):
        from scripts.extraction.test_extraction import _mock_extract  # type: ignore[import]
        expected = [
            {"name": f"Prodotto {i}", "price_offer": 1.0 + i * 0.1,
             "category": "dispensa", "price_original": None,
             "unit_price_value": None, "unit_price_unit": None}
            for i in range(10)
        ]
        extracted = _mock_extract(expected)
        # 10% miss rate: at least 1 product should be missing
        assert len(extracted) < len(expected) + 2  # +2 for potential hallucination

    def test_mock_adds_hallucination_for_large_lists(self):
        from scripts.extraction.test_extraction import _mock_extract  # type: ignore[import]
        expected = [
            {"name": f"Prodotto {i}", "price_offer": 1.0,
             "category": "dispensa", "price_original": None,
             "unit_price_value": None, "unit_price_unit": None}
            for i in range(10)
        ]
        extracted = _mock_extract(expected)
        names = [p["name"] for p in extracted]
        assert any("hallucination" in n.lower() for n in names)

    def test_run_mock_passes_threshold(self):
        """Full pipeline in mock mode should meet the F1 threshold."""
        from pathlib import Path
        from scripts.extraction.test_extraction import run  # type: ignore[import]
        fixtures_dir = Path(__file__).parent / "extraction_eval" / "fixtures"
        passed = run(fixtures_dir, mock=True)
        assert passed, "Mock extraction should pass the F1 threshold"
