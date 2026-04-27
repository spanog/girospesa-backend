"""
Convert submit_flyer.py output → ground truth fixture format.

submit_flyer.py produces a flat product list (whole flyer, all pages merged).
This script converts it to the *_ground_truth.json format expected by
compare_models.py and test_extraction.py.

The entire flyer is treated as one logical "page" with page_id "whole_flyer".
Categories are normalised to the controlled enum via normalizer.py.

Usage
-----
    python -m scripts.extraction.convert_submit_to_fixture \\
        --input conad-superstore-ground-truth.json \\
        --output tests/fixtures/extraction/superstore_calabria_ground_truth.json

    # Dry-run — prints fixture to stdout without writing:
    python -m scripts.extraction.convert_submit_to_fixture --input conad-superstore-ground-truth.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from services.extraction.normalizer import deduplicate_products, normalize_product

_GT_FIELDS = ("name", "brand", "category", "format", "price_offer", "price_original", "offer_notes")


def _to_gt_product(raw: dict) -> dict | None:
    """Normalize a product and keep only ground-truth schema fields. Returns None if invalid."""
    p = normalize_product(raw)
    if not p.get("name") or not p.get("price_offer"):
        return None
    return {k: p.get(k) for k in _GT_FIELDS}


def convert(input_path: Path, output_path: Path | None, dry_run: bool) -> None:
    data = json.loads(input_path.read_text(encoding="utf-8"))

    # Accept both submit_flyer format (has "products" key) and already-fixture format
    if "pages" in data:
        print("[WARN] Input already looks like a fixture (has 'pages' key). Aborting.")
        sys.exit(1)

    raw_products: list[dict] = data.get("products", [])
    if not raw_products:
        print("[ERROR] No 'products' array found in input.")
        sys.exit(1)

    supermarket = data.get("supermarket", "Unknown")
    source_pdf = data.get("source_file", input_path.name)

    gt_products = [p for raw in raw_products if (p := _to_gt_product(raw)) is not None]
    gt_products = deduplicate_products(gt_products)

    fixture = {
        "supermarket": supermarket,
        "source_pdf": Path(source_pdf).name,
        "description": (
            f"Ground truth for {supermarket} — converted from submit_flyer output. "
            "Human review recommended: fix wrong values, remove hallucinations, add missed products."
        ),
        "pages": [
            {
                "page_id": "whole_flyer",
                "page_number": None,
                "description": f"All {len(gt_products)} products from the entire flyer (merged, deduplicated)",
                "expected_products": gt_products,
            }
        ],
    }

    output_json = json.dumps(fixture, ensure_ascii=False, indent=2)

    if dry_run:
        print(output_json)
        return

    out = output_path or (
        _ROOT / "tests" / "fixtures" / "extraction"
        / f"{supermarket.lower().replace(' ', '_')}_ground_truth.json"
    )
    out.write_text(output_json, encoding="utf-8")
    print(f"✓ {len(gt_products)} products → {out}")
    print(f"  Supermarket : {supermarket}")
    print(f"  Source PDF  : {Path(source_pdf).name}")
    print(f"\n  ⚠  Review the fixture before running compare_models.py:")
    print(f"     - Fix wrong names, prices, categories")
    print(f"     - Remove hallucinated products")
    print(f"     - Add any products the model missed")
    print(f"\n  Then run:")
    print(f"     python -m scripts.extraction.compare_models \\")
    print(f"         --fixture {out} \\")
    print(f"         --pdf <flyer.pdf> \\")
    print(f"         --models llama3.2-vision qwen3.5:9b qwen3-vl:8b glm-ocr")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert submit_flyer.py output to ground truth fixture format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", type=Path, required=True, help="submit_flyer.py output JSON")
    parser.add_argument("--output", type=Path, help="Output fixture path (default: tests/fixtures/extraction/<slug>_ground_truth.json)")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout without writing")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"[ERROR] File not found: {args.input}")
        sys.exit(1)

    convert(args.input, args.output, args.dry_run)


if __name__ == "__main__":
    main()
