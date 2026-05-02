"""
Compare multiple Gemini models on extraction quality.

Usage:
    python -m scripts.extraction.compare_models \
        --fixture tests/extraction_eval/fixtures/example_ground_truth.json \
        --models gemma-4-31b-it gemini-2.5-pro \
        --pdf flyer.pdf
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

from services.extraction.normalizer import deduplicate_products, normalize_products
from services.extraction.pdf_utils import pdf_to_images_b64
from services.extraction.providers.gemini import GeminiProvider
from scripts.extraction.extraction_metrics import SCORED_FIELDS, SupermarketReport, build_report

_ROOT = Path(__file__).resolve().parents[2]


def _has_minimum_product_fields(raw: dict) -> bool:
    return bool(raw.get("name") and (raw.get("price_offer") or raw.get("price_current")))


def _extract_image(provider: GeminiProvider, image_bytes: bytes) -> list[dict]:
    products, retry_errors = provider.extract_products(image_bytes, "image/jpeg")
    for error in retry_errors:
        print(f"  [WARN] retry: {error}")
    return products


def _run_model_on_pdf(
    fixture: dict,
    provider: GeminiProvider,
    pdf_path: Path,
) -> tuple[SupermarketReport, float]:
    pdf_bytes = pdf_path.read_bytes()
    pages_b64 = pdf_to_images_b64(pdf_bytes)

    all_raw: list[dict] = []
    total_time = 0.0
    for img_b64 in pages_b64:
        t0 = time.time()
        raw = _extract_image(provider, base64.b64decode(img_b64))
        total_time += time.time() - t0
        all_raw.extend(raw)

    normalized = deduplicate_products(
        [p for p in normalize_products(all_raw) if _has_minimum_product_fields(p)]
    )
    expected = fixture["pages"][0]["expected_products"] if fixture["pages"] else []
    report = build_report(fixture["supermarket"], [("whole_flyer", expected, normalized)])
    avg_time = total_time / len(pages_b64) if pages_b64 else 0.0
    return report, avg_time


def _run_model(
    fixture: dict,
    provider: GeminiProvider,
    images_dir: Path,
) -> tuple[SupermarketReport, float]:
    pages_data: list[tuple[str, list[dict], list[dict]]] = []
    total_time = 0.0
    page_count = 0

    for page in fixture["pages"]:
        page_id = page["page_id"]
        expected = page["expected_products"]
        image_path = images_dir / f"{page_id}.jpg"
        if not image_path.exists():
            pages_data.append((page_id, expected, []))
            continue

        t0 = time.time()
        raw = _extract_image(provider, image_path.read_bytes())
        total_time += time.time() - t0
        page_count += 1
        normalized = deduplicate_products(
            [p for p in normalize_products(raw) if _has_minimum_product_fields(p)]
        )
        pages_data.append((page_id, expected, normalized))

    report = build_report(fixture["supermarket"], pages_data)
    avg_time = total_time / page_count if page_count else 0.0
    return report, avg_time


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


def _print_results(results: list[tuple[str, SupermarketReport, float]]) -> None:
    width = max(len(model) for model, _, _ in results) + 2
    print(f"\n{'Model':<{width}} {'Precision':>10} {'Recall':>8} {'F1':>8} {'sec/page':>10}")
    for model, report, avg_time in results:
        print(
            f"{model:<{width}}"
            f" {_pct(report.overall_product_precision):>10}"
            f" {_pct(report.overall_product_recall):>8}"
            f" {_pct(report.overall_product_f1):>8}"
            f" {avg_time:>9.1f}s"
        )

    print("\nField-level F1:")
    for model, report, _ in results:
        metrics = " ".join(f"{fname}={_pct(report.aggregate_field(fname).f1).strip()}" for fname in SCORED_FIELDS)
        print(f"{model:<{width}} {metrics}")


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Compare Gemini models on extraction quality")
    parser.add_argument("--fixture", type=Path, required=True, help="Ground truth fixture JSON")
    parser.add_argument("--models", nargs="+", required=True, help="Gemini models to compare")
    parser.add_argument("--pdf", type=Path, default=None, help="Whole-flyer PDF path")
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=_ROOT / "tests" / "extraction_eval" / "fixtures" / "images",
        help="Directory with <page_id>.jpg images",
    )
    parser.add_argument("--api-key", default=os.getenv("GOOGLE_API_KEY", ""))
    args = parser.parse_args()

    if not args.fixture.exists():
        print(f"[ERROR] Fixture not found: {args.fixture}")
        sys.exit(1)
    if args.pdf and not args.pdf.exists():
        print(f"[ERROR] PDF not found: {args.pdf}")
        sys.exit(1)
    if not args.api_key:
        print("[ERROR] GOOGLE_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    results: list[tuple[str, SupermarketReport, float]] = []
    for model in args.models:
        provider = GeminiProvider(api_key=args.api_key, model=model)
        if args.pdf:
            report, avg_time = _run_model_on_pdf(fixture, provider, args.pdf)
        else:
            report, avg_time = _run_model(fixture, provider, args.images_dir)
        results.append((model, report, avg_time))

    _print_results(results)


if __name__ == "__main__":
    main()
