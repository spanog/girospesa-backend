"""
Build a ground truth fixture from a real flyer PDF using Gemini.

Usage:
    python -m scripts.extraction.build_ground_truth \
        --pdf flyer.pdf \
        --supermarket "Conad" \
        --all-pages
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from services.extraction.normalizer import deduplicate_products, normalize_product
from services.extraction.providers.gemini import GeminiProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]


def _pdf_page_to_jpeg_bytes(pdf_bytes: bytes, page_index: int) -> bytes:
    try:
        import fitz
    except ImportError as exc:
        raise ImportError("pip install pymupdf") from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    matrix = fitz.Matrix(150 / 72, 150 / 72)
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=matrix)  # type: ignore[attr-defined]
    img_bytes = pix.tobytes("jpeg")
    doc.close()
    return img_bytes


def _slug(supermarket: str) -> str:
    return supermarket.lower().replace(" ", "_").replace("-", "_")


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    default_model = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
    default_api_key = os.getenv("GOOGLE_API_KEY", "")

    parser = argparse.ArgumentParser(description="Build ground truth fixture from a real flyer PDF")
    parser.add_argument("--pdf", type=Path, required=True, help="Path to flyer PDF")
    parser.add_argument("--supermarket", required=True, help="Supermarket name")
    pages_group = parser.add_mutually_exclusive_group(required=True)
    pages_group.add_argument("--pages", type=int, nargs="+", help="1-indexed page numbers to use")
    pages_group.add_argument("--all-pages", action="store_true", help="Process all pages in the PDF")
    parser.add_argument("--model", default=default_model, help="Gemini model name")
    parser.add_argument("--api-key", default=default_api_key, help="Google API key")
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=_ROOT / "tests" / "extraction_eval" / "fixtures" / "images",
        help="Directory to save page JPEG images",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path (default: tests/extraction_eval/fixtures/<slug>_ground_truth.json)",
    )
    parser.add_argument("--resume", action="store_true", help="Skip pages already present in output")
    args = parser.parse_args()

    if not args.pdf.exists():
        logger.error("PDF not found: %s", args.pdf)
        sys.exit(1)
    if not args.api_key:
        logger.error("Google API key required. Set GOOGLE_API_KEY or pass --api-key.")
        sys.exit(1)

    slug = _slug(args.supermarket)
    output_path = args.output or (_ROOT / "tests" / "extraction_eval" / "fixtures" / f"{slug}_ground_truth.json")
    args.images_dir.mkdir(parents=True, exist_ok=True)

    pdf_bytes = args.pdf.read_bytes()
    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        doc.close()
    except ImportError:
        logger.error("pip install pymupdf")
        sys.exit(1)

    pages: list[int] = list(range(1, total_pages + 1)) if args.all_pages else args.pages
    invalid = [p for p in pages if p < 1 or p > total_pages]
    if invalid:
        logger.error("Invalid page numbers (PDF has %d pages): %s", total_pages, invalid)
        sys.exit(1)

    existing_products: dict[int, list[dict]] = {}
    if args.resume and output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        for page in existing.get("pages", []):
            page_number = page.get("page_number")
            products = page.get("expected_products", [])
            if page_number and products:
                existing_products[page_number] = products

    provider = GeminiProvider(api_key=args.api_key, model=args.model)
    fixture_pages = []
    for page_num in sorted(set(pages)):
        page_id = f"{slug}_p{page_num:02d}"
        image_path = args.images_dir / f"{page_id}.jpg"

        if page_num in existing_products:
            fixture_pages.append(
                {
                    "page_id": page_id,
                    "page_number": page_num,
                    "description": f"Page {page_num} — pre-filled by {args.model}, needs human review",
                    "expected_products": existing_products[page_num],
                }
            )
            continue

        logger.info("Page %d/%d → %s", page_num, total_pages, page_id)
        image_bytes = _pdf_page_to_jpeg_bytes(pdf_bytes, page_num - 1)
        image_path.write_bytes(image_bytes)

        t0 = time.time()
        raw_products, retry_errors = provider.extract_products(image_bytes, "image/jpeg")
        for error in retry_errors:
            logger.warning(error)
        normalized = [normalize_product(p) for p in raw_products if p.get("name") and p.get("price_offer")]
        normalized = deduplicate_products(normalized)
        logger.info("  %d products in %.1fs", len(normalized), time.time() - t0)

        fixture_pages.append(
            {
                "page_id": page_id,
                "page_number": page_num,
                "description": f"Page {page_num} — pre-filled by {args.model}, needs human review",
                "expected_products": [
                    {
                        "name": p.get("name"),
                        "brand": p.get("brand"),
                        "category": p.get("category"),
                        "format": p.get("format"),
                        "price_offer": p.get("price_offer"),
                        "price_original": p.get("price_original"),
                        "offer_notes": p.get("offer_notes"),
                    }
                    for p in normalized
                ],
            }
        )

    fixture = {
        "supermarket": args.supermarket,
        "source_pdf": args.pdf.name,
        "description": f"Ground truth for {args.supermarket} — pre-filled by {args.model}, human-corrected",
        "pages": fixture_pages,
    }
    output_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Ground truth written → %s", output_path)


if __name__ == "__main__":
    main()
