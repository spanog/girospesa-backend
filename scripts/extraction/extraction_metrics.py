"""
Precision / recall / F1 computation for AI extraction evaluation.

Terminology:
  - expected  : ground truth products (manually annotated)
  - extracted : products returned by the extraction pipeline
  - match     : extracted product is "close enough" to an expected one

Matching strategy
-----------------
Two products match if their normalised names share ≥ NAME_MATCH_THRESHOLD
token overlap (Jaccard coefficient).  A single expected product can be
matched at most once (greedy, first-match wins).

Field-level evaluation
----------------------
For each matched pair we check whether the extracted value is "correct"
for each scored field:
  name            – matching threshold already met (always TP once matched)
  price_offer     – within ±PRICE_TOLERANCE_EUR
  price_original  – within ±PRICE_TOLERANCE_EUR (None/None is correct)
  unit_price_value – within ±PRICE_TOLERANCE_EUR (None/None is correct)
  unit_price_unit  – exact string match after normalisation
  category        – exact string match after normalisation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

NAME_MATCH_THRESHOLD = 0.4  # Jaccard token overlap required to consider names equivalent
PRICE_TOLERANCE_EUR = 0.05  # ±€0.05 tolerance for float price comparisons
SCORED_FIELDS = (
    "name",
    "price_offer",
    "price_original",
    "unit_price_value",
    "unit_price_unit",
    "category",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(text.strip().lower().split())


def _jaccard(a: str | None, b: str | None) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    union = ta | tb
    if not union:
        return 0.0
    return len(ta & tb) / len(union)


def _prices_match(expected: float | None, extracted: float | None) -> bool:
    if expected is None and extracted is None:
        return True
    if expected is None or extracted is None:
        return False
    return abs(expected - extracted) <= PRICE_TOLERANCE_EUR


def _field_correct(field_name: str, expected: dict, extracted: dict) -> bool:
    ev = expected.get(field_name)
    xv = extracted.get(field_name)
    if field_name == "name":
        return _jaccard(ev, xv) >= NAME_MATCH_THRESHOLD
    if field_name in ("price_offer", "price_original", "unit_price_value"):
        return _prices_match(ev, xv)
    # category: normalised string equality; None/None is correct
    if ev is None and xv is None:
        return True
    if ev is None or xv is None:
        return False
    return str(ev).strip().lower() == str(xv).strip().lower()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class FieldMetrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class PageResult:
    page_id: str
    expected_count: int
    extracted_count: int
    matched_count: int
    fields: dict[str, FieldMetrics] = field(default_factory=dict)

    @property
    def product_precision(self) -> float:
        return self.matched_count / self.extracted_count if self.extracted_count else 0.0

    @property
    def product_recall(self) -> float:
        return self.matched_count / self.expected_count if self.expected_count else 0.0

    @property
    def product_f1(self) -> float:
        p, r = self.product_precision, self.product_recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class SupermarketReport:
    supermarket: str
    pages: list[PageResult] = field(default_factory=list)

    def aggregate_field(self, fname: str) -> FieldMetrics:
        agg = FieldMetrics()
        for page in self.pages:
            fm = page.fields.get(fname, FieldMetrics())
            agg.tp += fm.tp
            agg.fp += fm.fp
            agg.fn += fm.fn
        return agg

    @property
    def overall_product_precision(self) -> float:
        matched = sum(p.matched_count for p in self.pages)
        extracted = sum(p.extracted_count for p in self.pages)
        return matched / extracted if extracted else 0.0

    @property
    def overall_product_recall(self) -> float:
        matched = sum(p.matched_count for p in self.pages)
        expected = sum(p.expected_count for p in self.pages)
        return matched / expected if expected else 0.0

    @property
    def overall_product_f1(self) -> float:
        p, r = self.overall_product_precision, self.overall_product_recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


# ---------------------------------------------------------------------------
# Core evaluation function
# ---------------------------------------------------------------------------

def evaluate_page(
    page_id: str,
    expected: Sequence[dict],
    extracted: Sequence[dict],
) -> PageResult:
    """Compare extracted products against ground truth for a single page."""
    unmatched_expected = list(expected)
    matched_pairs: list[tuple[dict, dict]] = []

    for ext_product in extracted:
        best_idx: int | None = None
        best_score = NAME_MATCH_THRESHOLD - 1e-9  # must beat threshold

        for idx, exp_product in enumerate(unmatched_expected):
            score = _jaccard(exp_product.get("name"), ext_product.get("name"))
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None:
            matched_pairs.append((unmatched_expected.pop(best_idx), ext_product))

    result = PageResult(
        page_id=page_id,
        expected_count=len(expected),
        extracted_count=len(extracted),
        matched_count=len(matched_pairs),
    )

    for fname in SCORED_FIELDS:
        fm = FieldMetrics()
        for exp_p, ext_p in matched_pairs:
            if _field_correct(fname, exp_p, ext_p):
                fm.tp += 1
            else:
                fm.fp += 1  # extracted something wrong
                fm.fn += 1  # missed the correct value
        # Unmatched expected products → FN for every field
        fm.fn += len(unmatched_expected)
        # Unmatched extracted products → FP for every field
        unmatched_ext = len(extracted) - len(matched_pairs)
        fm.fp += unmatched_ext
        result.fields[fname] = fm

    return result


def build_report(
    supermarket: str,
    pages: list[tuple[str, list[dict], list[dict]]],
) -> SupermarketReport:
    """Build a full SupermarketReport from (page_id, expected, extracted) tuples."""
    report = SupermarketReport(supermarket=supermarket)
    for page_id, expected, extracted in pages:
        report.pages.append(evaluate_page(page_id, expected, extracted))
    return report
