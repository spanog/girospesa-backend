"""
ExtractionService — on-demand AI extraction pipeline.

Runs inside the backend as a FastAPI BackgroundTask.

Pipeline:
  1. Fetch flyer row from DB
  2. Download file bytes
  3. Extract products via configured LLM provider
  4. Normalize + deduplicate
  5. Upsert canonical products
  6. Insert draft offers (is_confirmed=False)
  7. Update flyer status to 'done'

On any failure: set flyer status='error', log ERROR event.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import requests

from core.config import settings
from core.database import get_supabase
from services.extraction.normalizer import deduplicate_products, normalize_product
from services.extraction.pdf_utils import count_pdf_pages, is_pdf, mime_type_for_filename
from services.extraction.providers import ExtractionProvider, get_provider
from services.extraction.extraction_log import ERROR, SUCCESS, WARNING, log_event

logger = logging.getLogger(__name__)

_FLYER_SELECT = "id, file_url, file_name, supermarket_id, supermarket_name, valid_from, valid_to"


class ExtractionService:
    def __init__(
        self,
        provider: ExtractionProvider | None = None,
        supabase_factory: Callable[[], object] | None = None,
    ) -> None:
        self._provider: ExtractionProvider = provider or get_provider(settings)
        self._supabase_factory = supabase_factory or get_supabase

    def run(self, flyer_id: str) -> None:
        """Download → extract → normalize → upsert products → insert draft offers."""
        sb = self._supabase_factory()
        flyer = self._fetch_flyer(sb, flyer_id)
        supermarket_id = flyer.get("supermarket_id")
        supermarket_name = flyer.get("supermarket_name") or "Sconosciuto"
        t_start = time.time()

        logger.info(
            "Starting extraction flyer %s (%s — %s)",
            flyer_id,
            supermarket_name,
            flyer.get("file_name"),
        )

        try:
            self._run_pipeline(sb, flyer, supermarket_id, supermarket_name, t_start)
        except Exception as exc:
            self._handle_error(sb, flyer_id, supermarket_id, supermarket_name, exc, t_start)

    def _run_pipeline(
        self,
        sb: object,
        flyer: dict,
        supermarket_id: str | None,
        supermarket_name: str,
        t_start: float,
    ) -> None:
        flyer_id = flyer["id"]
        content = self._download_file(sb, flyer["file_url"])
        file_name = flyer.get("file_name", "")
        mime_type = mime_type_for_filename(file_name)
        pages_count = count_pdf_pages(content) if is_pdf(file_name) else 1

        sb.table("flyers").update({  # type: ignore[union-attr]
            "extraction_metadata": {"stage": "extracting", "pages_total": pages_count},
        }).eq("id", flyer_id).execute()

        logger.info(
            "  Sending to %s (%s, %d page(s))…",
            settings.llm_provider.upper(),
            mime_type,
            pages_count,
        )
        all_products, retry_errors = self._provider.extract_products(content, mime_type)

        sb.table("flyers").update({  # type: ignore[union-attr]
            "extraction_metadata": {
                "stage": "saving",
                "pages_total": pages_count,
                "products_found": len(all_products),
            },
        }).eq("id", flyer_id).execute()

        if retry_errors:
            log_event(
                sb,
                event_type=WARNING,
                message=f"{len(retry_errors)} retry attempt(s) before result",
                flyer_id=flyer_id,
                supermarket_id=supermarket_id,
                supermarket_name=supermarket_name,
                details={"retry_errors": retry_errors},
            )

        if not all_products:
            raise ValueError("No products extracted from flyer")

        normalized = [
            p for p in (normalize_product(r) for r in all_products)
            if p.get("name") and p.get("price_offer")
        ]
        normalized = deduplicate_products(normalized)

        if not normalized:
            raise ValueError("No valid products after normalization")

        offer_rows = self._build_offer_rows(sb, normalized, flyer, supermarket_id, supermarket_name)

        if offer_rows:
            sb.table("offers").insert(offer_rows).execute()  # type: ignore[union-attr]

        elapsed = int(time.time() - t_start)
        sb.table("flyers").update({  # type: ignore[union-attr]
            "status": "done",
            "products_count": len(offer_rows),
            "pages_count": pages_count,
            "extraction_metadata": None,
        }).eq("id", flyer_id).execute()

        logger.info(
            "Done flyer %s (%s) — %d products in %ds",
            flyer_id,
            supermarket_name,
            len(offer_rows),
            elapsed,
        )
        log_event(
            sb,
            event_type=SUCCESS,
            message=f"Extraction completed: {len(offer_rows)} products in {elapsed}s",
            flyer_id=flyer_id,
            supermarket_id=supermarket_id,
            supermarket_name=supermarket_name,
            details={
                "products_count": len(offer_rows),
                "pages_count": pages_count,
                "elapsed_seconds": elapsed,
            },
        )

    def _build_offer_rows(
        self,
        sb: object,
        normalized: list[dict],
        flyer: dict,
        supermarket_id: str | None,
        supermarket_name: str,
    ) -> list[dict]:
        offer_rows: list[dict] = []
        for p in normalized:
            product_id = self._upsert_product(sb, {
                "name": p["name"],
                "brand": p.get("brand"),
                "category": p.get("category"),
                "subcategory": p.get("subcategory"),
                "format": p.get("format"),
            })
            offer_rows.append(self._build_offer_row(product_id, p, flyer, supermarket_id, supermarket_name))
        return offer_rows

    def _fetch_flyer(self, sb: object, flyer_id: str) -> dict:
        result = (
            sb.table("flyers")  # type: ignore[union-attr]
            .select(_FLYER_SELECT)
            .eq("id", flyer_id)
            .single()
            .execute()
        )
        if not result.data:
            raise ValueError(f"Flyer not found: {flyer_id}")
        return result.data

    def _download_file(self, sb: object, file_url: str) -> bytes:
        # flyers bucket is private — use storage SDK (service role) instead of HTTP GET
        prefix = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/flyers/"
        storage_path = file_url.removeprefix(prefix)
        if storage_path == file_url:
            # fallback: signed-URL path or unknown format — try HTTP
            resp = requests.get(file_url, timeout=30)
            resp.raise_for_status()
            return resp.content
        return bytes(sb.storage.from_("flyers").download(storage_path))  # type: ignore[union-attr]

    def _upsert_product(self, sb: object, product_row: dict) -> str:
        """
        Upsert a canonical product row and return its stable UUID.

        Uses ON CONFLICT (name, brand, format) DO UPDATE to always get the row
        back in the RETURNING clause, even when the product already existed.
        Falls back to a SELECT when the upsert returns an empty result set.

        Raises ValueError if the product cannot be found or created.
        """
        result = (
            sb.table("products")  # type: ignore[union-attr]
            .upsert(product_row, on_conflict="name,brand,format")
            .execute()
        )
        if result.data:
            return result.data[0]["id"]

        # Fallback: SELECT with NULL-safe filters
        query = sb.table("products").select("id").eq("name", product_row["name"])  # type: ignore[union-attr]
        brand = product_row.get("brand")
        fmt = product_row.get("format")
        query = query.is_("brand", "null") if brand is None else query.eq("brand", brand)
        query = query.is_("format", "null") if fmt is None else query.eq("format", fmt)
        existing = query.limit(1).execute()

        if not existing.data:
            raise ValueError(
                f"Product not found after upsert: name={product_row['name']!r} "
                f"brand={brand!r} format={fmt!r}"
            )
        return existing.data[0]["id"]

    def _build_offer_row(
        self,
        product_id: str,
        p: dict,
        flyer: dict,
        supermarket_id: str | None,
        supermarket_name: str,
    ) -> dict:
        return {
            "product_id": product_id,
            "supermarket_id": supermarket_id,
            "supermarket_name": supermarket_name,
            "flyer_id": flyer["id"],
            "price_offer": p["price_offer"],
            "price_original": p.get("price_original"),
            "unit_price": p.get("unit_price"),
            "unit_price_value": p.get("unit_price_value"),
            "unit_price_unit": p.get("unit_price_unit"),
            "offer_notes": p.get("offer_notes"),
            "valid_from": flyer.get("valid_from") or p.get("valid_from"),
            "valid_to": flyer.get("valid_to") or p.get("valid_to"),
            "is_confirmed": False,
        }

    def _handle_error(
        self,
        sb: object,
        flyer_id: str,
        supermarket_id: str | None,
        supermarket_name: str,
        exc: Exception,
        t_start: float,
    ) -> None:
        elapsed = int(time.time() - t_start)
        logger.error(
            "Extraction failed flyer %s (%s) after %ds: %s",
            flyer_id,
            supermarket_name,
            elapsed,
            exc,
        )
        sb.table("flyers").update({  # type: ignore[union-attr]
            "status": "error",
            "error_message": str(exc)[:500],
        }).eq("id", flyer_id).execute()
        log_event(
            sb,
            event_type=ERROR,
            message=f"Extraction failed after {elapsed}s: {exc!s:.500}",
            flyer_id=flyer_id,
            supermarket_id=supermarket_id,
            supermarket_name=supermarket_name,
            details={"error": str(exc), "elapsed_seconds": elapsed},
        )
