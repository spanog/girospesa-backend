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
from types import SimpleNamespace

from services.extraction.normalizer import deduplicate_products, normalize_products
from services.extraction.pdf_utils import count_pdf_pages, is_pdf, mime_type_for_filename
from services.extraction.providers import ExtractionProvider, GeminiProvider, get_provider
from services.extraction.providers.base import PdfChunkExtractionError
from services.extraction.providers.ollama_client import OllamaExtractionError
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
    default_api_key = os.getenv("GOOGLE_API_KEY", "")

    parser = argparse.ArgumentParser(description="Extract product offers from a local flyer file.")
    parser.add_argument("file", help="Path to PDF or image file")
    parser.add_argument("-s", "--supermarket", default="Sconosciuto", help="Supermarket name")
    parser.add_argument("--valid-from", dest="valid_from", help="Offer start date YYYY-MM-DD")
    parser.add_argument("--valid-to", dest="valid_to", help="Offer end date YYYY-MM-DD")
    parser.add_argument("-o", "--output", help="Output JSON file path")
    parser.add_argument("--model", default=None, help="Model tag for selected provider")
    parser.add_argument("--provider", choices=("gemini", "ollama"), default=os.getenv("LLM_PROVIDER", "gemini"))
    _add_ollama_args(parser)
    parser.add_argument("--api-key", default=default_api_key, help="Google API key (default: $GOOGLE_API_KEY)")
    return parser.parse_args(argv)


def _add_ollama_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chunk-pages", type=int, choices=(1, 2), default=int(os.getenv("OLLAMA_CHUNK_SIZE_PAGES", "1")))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "600")))
    parser.add_argument("--dpi", type=int, default=int(os.getenv("OLLAMA_IMAGE_DPI", "150")))
    parser.add_argument("--context", type=int, default=int(os.getenv("OLLAMA_CONTEXT_TOKENS", "16384")))
    parser.add_argument("--max-output", type=int, default=int(os.getenv("OLLAMA_MAX_OUTPUT_TOKENS", "8192")))


def _provider(args: argparse.Namespace) -> tuple[ExtractionProvider, str]:
    if args.provider == "gemini":
        if not args.api_key:
            raise ValueError("Google API key required for Gemini; use --provider ollama for local extraction")
        model = args.model or os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
        return GeminiProvider(api_key=args.api_key, model=model), model
    if args.timeout <= 0 or args.context < 1024 or args.max_output <= 0 or not 72 <= args.dpi <= 300:
        raise ValueError("Invalid Ollama timeout, context, output budget or DPI (72–300)")
    model = args.model or os.getenv("OLLAMA_MODEL", "gemma4:31b-mlx")
    config = SimpleNamespace(llm_provider="ollama", ollama_model=model, ollama_base_url=args.ollama_url,
                             ollama_timeout_seconds=args.timeout, ollama_image_dpi=args.dpi,
                             ollama_context_tokens=args.context, ollama_max_output_tokens=args.max_output,
                             ollama_chunk_size_pages=args.chunk_pages)
    return get_provider(config), model


def _source(args: argparse.Namespace) -> Path:
    path = Path(args.file)
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported format: {path.suffix}")
    return path


def _extract(provider: ExtractionProvider, path: Path) -> tuple[list[dict], int]:
    mime_type = mime_type_for_filename(path.name)
    content = path.read_bytes()
    pages = count_pdf_pages(content) if is_pdf(path.name) else 1
    products, errors = provider.extract_products(content, mime_type)
    for error in errors:
        logger.warning(error)
    normalized = [p for p in normalize_products(products) if p.get("name") and p.get("price_offer")]
    unique = [_ensure_format_label(p) for p in deduplicate_products(normalized)]
    if not unique:
        raise ValueError("No products extracted from file")
    return unique, pages


def _write_output(args: argparse.Namespace, source: Path, model: str, products: list[dict], pages: int) -> None:
    output = {
        "supermarket": args.supermarket, "source_file": str(source.resolve()),
        "valid_from": args.valid_from, "valid_to": args.valid_to,
        "model": model, "provider": args.provider, "pages_count": pages,
        "products_count": len(products), "products": products,
    }
    destination = Path(args.output) if args.output else Path(f"extracted_{source.stem}.json")
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Done — %d product(s) extracted → %s", len(products), destination)


def main(argv: Sequence[str] | None = None) -> None:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    try:
        args = parse_args(argv)
        source = _source(args)
        provider, model = _provider(args)
        logger.info("Extracting %s with %s (%s)", source.name, args.provider, model)
        products, pages = _extract(provider, source)
        _write_output(args, source, model, products, pages)
    except (ValueError, OSError, PdfChunkExtractionError, OllamaExtractionError) as exc:
        logger.error("Extraction failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
