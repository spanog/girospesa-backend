"""
submit_flyer.py — CLI to process a local flyer file without Supabase.

Extracts product offers from a PDF or image using the backend extraction stack.

Usage:
    python -m scripts.extraction.submit_flyer <file> [options]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Sequence

from services.extraction.normalizer import deduplicate_products, normalize_products
from services.extraction.pdf_utils import count_pdf_pages, is_pdf, mime_type_for_filename
from services.extraction.providers import GeminiProvider
from services.product_format import build_extraction_format_bundle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}


def _ensure_format_label(product: dict) -> dict:
    if product.get("format_label") or not product.get("format"):
        return product
    bundle = build_extraction_format_bundle(product["format"])
    enriched = dict(product)
    enriched["format_label"] = bundle.format_label
    return enriched


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_gemini_model = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
    default_api_key = os.getenv("GOOGLE_API_KEY", "")

    parser = argparse.ArgumentParser(description="Extract product offers from a local flyer file.")
    parser.add_argument("file", help="Path to PDF or image file")
    parser.add_argument("-s", "--supermarket", default="Sconosciuto", help="Supermarket name")
    parser.add_argument("--valid-from", dest="valid_from", help="Offer start date YYYY-MM-DD")
    parser.add_argument("--valid-to", dest="valid_to", help="Offer end date YYYY-MM-DD")
    parser.add_argument("-o", "--output", help="Output JSON file path")
    parser.add_argument("--model", default=None, help=f"Gemini model name (default: {default_gemini_model})")
    parser.add_argument("--api-key", default=default_api_key, help="Google API key (default: $GOOGLE_API_KEY)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    default_gemini_model = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
    args = parse_args(argv)

    source_path = Path(args.file)
    if not source_path.exists():
        logger.error("File not found: %s", args.file)
        sys.exit(1)

    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        logger.error("Unsupported format '%s'. Supported: %s", suffix, ", ".join(sorted(SUPPORTED_EXTENSIONS)))
        sys.exit(1)

    if not args.api_key:
        logger.error("Google API key required. Set GOOGLE_API_KEY env var or pass --api-key.")
        sys.exit(1)

    model = args.model or default_gemini_model
    provider = GeminiProvider(api_key=args.api_key, model=model)

    file_bytes = source_path.read_bytes()
    mime_type = mime_type_for_filename(source_path.name)
    pages_count = count_pdf_pages(file_bytes) if is_pdf(source_path.name) else 1
    logger.info("Sending %s to Gemini (%s, %d page(s))", source_path.name, mime_type, pages_count)

    all_products, retry_errors = provider.extract_products(file_bytes, mime_type)
    for error in retry_errors:
        logger.warning(error)

    normalized = normalize_products(all_products)
    normalized = [p for p in normalized if p.get("name") and p.get("price_offer")]
    unique = [_ensure_format_label(product) for product in deduplicate_products(normalized)]
    if not unique:
        logger.error("No products extracted from file.")
        sys.exit(1)

    output = {
        "supermarket": args.supermarket,
        "source_file": str(source_path.resolve()),
        "valid_from": args.valid_from,
        "valid_to": args.valid_to,
        "model": model,
        "pages_count": pages_count,
        "products_count": len(unique),
        "products": unique,
    }

    output_path = Path(args.output) if args.output else Path(f"extracted_{source_path.stem}.json")
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Done — %d product(s) extracted → %s", len(unique), output_path)


if __name__ == "__main__":
    main()
