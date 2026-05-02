"""
Extraction quality evaluation script.

Usage:
  python -m scripts.extraction.test_extraction --mock
  python -m scripts.extraction.test_extraction --live --fixtures-dir tests/extraction_eval/fixtures
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from services.extraction.normalizer import deduplicate_products, normalize_products
from services.extraction.providers.gemini import GeminiProvider
from scripts.extraction.extraction_metrics import SCORED_FIELDS, SupermarketReport, build_report

MIN_F1_THRESHOLD = 0.60
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _mock_extract(expected_products: list[dict]) -> list[dict]:
    extracted: list[dict] = []
    for idx, product in enumerate(expected_products):
        if idx % 10 == 9:
            continue
        candidate = dict(product)
        if idx % 10 == 4 and product.get("price_offer") is not None:
            candidate["price_offer"] = round(product["price_offer"] + 0.20, 2)
        if idx % 20 == 7:
            candidate["category"] = "altro"
        extracted.append(candidate)
    if len(expected_products) >= 5:
        extracted.append(
            {
                "name": "Prodotto non esistente hallucination",
                "brand": None,
                "category": "dispensa",
                "format": None,
                "price_offer": 1.99,
                "price_original": None,
                "offer_notes": None,
            }
        )
    return extracted


def _live_extract(page_id: str, model: str, api_key: str, images_dir: Path) -> list[dict]:
    image_path = images_dir / f"{page_id}.jpg"
    if not image_path.exists():
        print(f"  [SKIP] {page_id} — image not found: {image_path}")
        return []
    provider = GeminiProvider(api_key=api_key, model=model)
    products, _retry_errors = provider.extract_products(image_path.read_bytes(), "image/jpeg")
    normalized = [p for p in normalize_products(products) if p.get("name") and p.get("price_offer")]
    return deduplicate_products(normalized)


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def print_report(report: SupermarketReport) -> None:
    print(f"\n{report.supermarket}")
    for page in report.pages:
        print(
            f"  {page.page_id:<30} P={_fmt_pct(page.product_precision)} "
            f"R={_fmt_pct(page.product_recall)} F1={_fmt_pct(page.product_f1)}"
        )
    for field_name in SCORED_FIELDS:
        metrics = report.aggregate_field(field_name)
        print(f"  {field_name:<16} F1={_fmt_pct(metrics.f1)}")


def print_summary(reports: list[SupermarketReport], threshold: float) -> bool:
    passed = True
    for report in reports:
        status = "PASS" if report.overall_product_f1 >= threshold else "FAIL"
        print(f"{status} {report.supermarket}: F1={_fmt_pct(report.overall_product_f1)}")
        passed = passed and report.overall_product_f1 >= threshold
    return passed


def load_fixtures(fixtures_dir: Path) -> list[dict]:
    fixtures = []
    for path in sorted(fixtures_dir.glob("*_ground_truth.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if "pages" not in fixture and "products" in fixture:
            fixture = {
                "supermarket": fixture.get("supermarket", path.stem),
                "source_pdf": fixture.get("source_file", path.name),
                "description": fixture.get("description", "Converted flat fixture"),
                "pages": [
                    {
                        "page_id": "whole_flyer",
                        "page_number": None,
                        "description": "Whole flyer",
                        "expected_products": fixture.get("products", []),
                    }
                ],
            }
        fixtures.append(fixture)
    return fixtures


def run(
    fixtures_dir: Path,
    mock: bool,
    model: str = "gemma-4-31b-it",
    api_key: str = "",
    images_dir: Path | None = None,
) -> bool:
    fixtures = load_fixtures(fixtures_dir)
    if not fixtures:
        print(f"No *_ground_truth.json fixtures found in {fixtures_dir}")
        return False

    effective_images_dir = images_dir or (fixtures_dir / "images")
    reports: list[SupermarketReport] = []
    for fixture in fixtures:
        pages_data = []
        for page in fixture["pages"]:
            expected = page["expected_products"]
            if mock:
                extracted = _mock_extract(expected)
            else:
                extracted = _live_extract(page["page_id"], model, api_key, effective_images_dir)
            pages_data.append((page["page_id"], expected, extracted))
        report = build_report(fixture["supermarket"], pages_data)
        print_report(report)
        reports.append(report)

    return print_summary(reports, MIN_F1_THRESHOLD)


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Extraction quality evaluation")
    parser.add_argument("--mock", action="store_true", help="Run synthetic extraction")
    parser.add_argument("--live", action="store_true", help="Run real extraction (requires GOOGLE_API_KEY)")
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=_BACKEND_ROOT / "tests" / "extraction_eval" / "fixtures",
    )
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemma-4-31b-it"))
    parser.add_argument("--api-key", default=os.getenv("GOOGLE_API_KEY", ""))
    parser.add_argument("--images-dir", type=Path)
    args = parser.parse_args()

    if args.mock == args.live:
        parser.error("Choose exactly one mode: --mock or --live")
    if args.live and not args.api_key:
        parser.error("--api-key or GOOGLE_API_KEY is required with --live")

    ok = run(
        args.fixtures_dir,
        mock=args.mock,
        model=args.model,
        api_key=args.api_key,
        images_dir=args.images_dir,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
