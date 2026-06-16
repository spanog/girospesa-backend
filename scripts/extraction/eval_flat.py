"""
Evaluate extraction against a flat ground-truth JSON (no per-page split).

Usage:
    python -m scripts.extraction.eval_flat \
        --pdf volantino-conad-ridotto.pdf \
        --ground-truth volantino-conad-ridotto-ground-truth.json \
        [--model models/gemma-4-31b-it]

The ground-truth JSON must have a top-level "products" list.
Requires GOOGLE_API_KEY in environment or .env file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Load .env from project root so GOOGLE_API_KEY is available without export
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

from services.extraction.normalizer import deduplicate_products, normalize_products  # noqa: E402
from services.extraction.providers.gemini import GeminiProvider  # noqa: E402
from scripts.extraction.extraction_metrics import (  # noqa: E402
    SCORED_FIELDS,
    FieldMetrics,
    evaluate_page,
)


def _has_minimum_product_fields(raw: dict) -> bool:
    return bool(raw.get("name") and (raw.get("price_offer") or raw.get("price_current")))


def _fmt(v: float) -> str:
    return f"{v * 100:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Flat-format extraction eval")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--model", default="gemma-4-31b-it")
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    pdf_path = args.pdf if args.pdf.is_absolute() else _ROOT / args.pdf
    gt_path = args.ground_truth if args.ground_truth.is_absolute() else _ROOT / args.ground_truth

    print(f"PDF          : {pdf_path}")
    print(f"Ground truth : {gt_path}")
    print(f"Model        : {args.model}")

    with gt_path.open(encoding="utf-8") as f:
        gt = json.load(f)
    expected = gt["products"]
    print(f"\nGround truth : {len(expected)} products")

    print("\nRunning extraction…")
    t0 = time.time()
    provider = GeminiProvider(api_key=api_key, model=args.model)
    pdf_bytes = pdf_path.read_bytes()
    raw_products, errors = provider.extract_products(pdf_bytes, "application/pdf")
    elapsed = time.time() - t0

    if errors:
        print(f"\nRetry errors during extraction:")
        for e in errors:
            print(f"  {e}")

    extracted = deduplicate_products(
        [p for p in normalize_products(raw_products) if _has_minimum_product_fields(p)]
    )

    print(f"Extracted    : {len(raw_products)} raw → {len(extracted)} after normalize+dedup")
    print(f"Elapsed      : {elapsed:.0f}s")

    result = evaluate_page("all", expected, extracted)

    print(f"\n{'─'*56}")
    print(f"  Product-level")
    print(f"{'─'*56}")
    print(f"  Expected  : {result.expected_count}")
    print(f"  Extracted : {result.extracted_count}")
    print(f"  Matched   : {result.matched_count}")
    print(f"  Precision : {_fmt(result.product_precision)}")
    print(f"  Recall    : {_fmt(result.product_recall)}")
    print(f"  F1        : {_fmt(result.product_f1)}")

    print(f"\n{'─'*56}")
    print(f"  Field-level accuracy (on matched pairs)")
    print(f"{'─'*56}")
    for fname in SCORED_FIELDS:
        fm = result.fields.get(fname, FieldMetrics())
        print(f"  {fname:<16} P={_fmt(fm.precision)}  R={_fmt(fm.recall)}  F1={_fmt(fm.f1)}"
              f"  (TP={fm.tp} FP={fm.fp} FN={fm.fn})")

    threshold = 0.60
    status = "PASS ✅" if result.product_f1 >= threshold else "FAIL ❌"
    print(f"\n  Threshold F1 ≥ {_fmt(threshold)} → {status}")
    sys.exit(0 if result.product_f1 >= threshold else 1)


if __name__ == "__main__":
    main()
