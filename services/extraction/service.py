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
from rapidfuzz import fuzz

from services.extraction.normalizer import (
    deduplicate_products,
    expand_products,
    json_size_bytes,
    normalize_for_comparison,
    normalize_product,
)
from services.extraction.pdf_utils import count_pdf_pages, is_pdf, mime_type_for_filename
from services.extraction.providers import ExtractionProvider, get_provider
from services.extraction.extraction_log import ERROR, SUCCESS, WARNING, log_event
from services.product_format import NormalizedFormatBundle
from services.push_notify import notify_extraction_complete

logger = logging.getLogger(__name__)

_FLYER_SELECT = "id, file_url, file_name, supermarket_id, supermarket_name, valid_from, valid_to, user_id"
_PRODUCT_COLUMNS = (
    "name",
    "brand",
    "category",
    "subcategory",
    "format",
    "format_key",
    "format_label",
)


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

        user_id: str | None = flyer.get("user_id")
        try:
            self._run_pipeline(sb, flyer, supermarket_id, supermarket_name, t_start)
        except Exception as exc:
            self._handle_error(sb, flyer_id, supermarket_id, supermarket_name, exc, t_start, user_id=user_id)

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
        extracting_chunk_metadata = self._chunk_metadata(
            mime_type,
            pages_count,
            chunks_completed=0,
            chunk_failures=0,
        )

        sb.table("flyers").update({  # type: ignore[union-attr]
            "extraction_metadata": {
                "stage": "extracting",
                "pages_total": pages_count,
                **extracting_chunk_metadata,
            },
        }).eq("id", flyer_id).execute()

        logger.info(
            "  Sending to %s (%s, %d page(s))…",
            settings.llm_provider.upper(),
            mime_type,
            pages_count,
        )
        provider_started_at = time.perf_counter()
        all_products, retry_errors = self._provider.extract_products(content, mime_type)
        provider_seconds = time.perf_counter() - provider_started_at
        success_chunk_metadata = self._chunk_metadata(
            mime_type,
            pages_count,
            chunks_completed=None,
            chunk_failures=0,
        )

        sb.table("flyers").update({  # type: ignore[union-attr]
            "extraction_metadata": {
                "stage": "saving",
                "pages_total": pages_count,
                "products_found": len(all_products),
                "provider_seconds": round(provider_seconds, 3),
                **success_chunk_metadata,
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

        variant_started_at = time.perf_counter()
        expanded_products = expand_products(all_products)
        variant_expansion_seconds = time.perf_counter() - variant_started_at

        normalized_started_at = time.perf_counter()
        normalized = [
            normalize_product(candidate)
            for candidate in expanded_products
            if candidate.get("name") and (candidate.get("price_offer") or candidate.get("price_current"))
        ]
        normalization_seconds = time.perf_counter() - normalized_started_at

        dedupe_started_at = time.perf_counter()
        normalized = [p for p in normalized if p.get("name") and p.get("price_offer")]
        normalized = deduplicate_products(normalized)
        dedupe_seconds = time.perf_counter() - dedupe_started_at

        if not normalized:
            raise ValueError("No valid products after normalization")

        avg_compact_bytes, avg_normalized_bytes = self._format_size_metrics(expanded_products)

        saving_metadata = {
            "stage": "saving",
            "pages_total": pages_count,
            "products_found": len(all_products),
            "products_raw_count": len(all_products),
            "products_after_variants_count": len(expanded_products),
            "products_unique_count": len(normalized),
            "provider_seconds": round(provider_seconds, 3),
            "variant_expansion_seconds": round(variant_expansion_seconds, 3),
            "normalization_seconds": round(normalization_seconds, 3),
            "dedupe_seconds": round(dedupe_seconds, 3),
            "avg_format_bytes_compact": round(avg_compact_bytes, 2),
            "avg_format_bytes_normalized": round(avg_normalized_bytes, 2),
            **success_chunk_metadata,
        }
        sb.table("flyers").update({  # type: ignore[union-attr]
            "extraction_metadata": saving_metadata,
        }).eq("id", flyer_id).execute()

        product_upsert_started_at = time.perf_counter()
        product_ids = self._upsert_products_batch(sb, normalized)
        product_upsert_seconds = time.perf_counter() - product_upsert_started_at

        offer_insert_started_at = time.perf_counter()
        offer_rows = self._build_offer_rows(product_ids, normalized, flyer, supermarket_id, supermarket_name)

        if offer_rows:
            sb.table("offers").insert(offer_rows).execute()  # type: ignore[union-attr]
        offer_insert_seconds = time.perf_counter() - offer_insert_started_at

        total_seconds = time.time() - t_start
        elapsed = int(total_seconds)
        summary_metadata = {
            "provider_seconds": round(provider_seconds, 3),
            "variant_expansion_seconds": round(variant_expansion_seconds, 3),
            "normalization_seconds": round(normalization_seconds, 3),
            "dedupe_seconds": round(dedupe_seconds, 3),
            "product_upsert_seconds": round(product_upsert_seconds, 3),
            "offer_insert_seconds": round(offer_insert_seconds, 3),
            "total_seconds": round(total_seconds, 3),
            "products_raw_count": len(all_products),
            "products_after_variants_count": len(expanded_products),
            "products_unique_count": len(normalized),
            "avg_format_bytes_compact": round(avg_compact_bytes, 2),
            "avg_format_bytes_normalized": round(avg_normalized_bytes, 2),
            **success_chunk_metadata,
        }
        sb.table("flyers").update({  # type: ignore[union-attr]
            "status": "done",
            "products_count": len(offer_rows),
            "pages_count": pages_count,
            "extraction_metadata": summary_metadata,
        }).eq("id", flyer_id).execute()

        logger.info(
            "Done flyer %s (%s) — %d products in %ds [provider=%.3fs variants=%.3fs normalize=%.3fs dedupe=%.3fs upsert=%.3fs offers=%.3fs]",
            flyer_id,
            supermarket_name,
            len(offer_rows),
            elapsed,
            provider_seconds,
            variant_expansion_seconds,
            normalization_seconds,
            dedupe_seconds,
            product_upsert_seconds,
            offer_insert_seconds,
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
                **summary_metadata,
            },
        )
        if flyer.get("user_id"):
            notify_extraction_complete(
                sb,
                flyer_id=flyer_id,
                user_id=flyer["user_id"],
                success=True,
                supermarket_name=supermarket_name,
                products_count=len(offer_rows),
            )

    def _conflict_key(self, row: dict) -> tuple[str, str | None, str]:
        return (row["name"], row.get("brand"), row["format_key"])

    def _product_row_from_normalized(self, row: dict) -> dict:
        return {column: row.get(column) for column in _PRODUCT_COLUMNS}

    def _find_similar_product(
        self,
        incoming: dict,
        candidates: list[dict],
    ) -> str | None:
        """Return existing product_id if a candidate is similar enough to incoming, else None.

        format_key must already match exactly (candidates are pre-filtered by format_key).
        Brand uses ratio() on diacritic-normalized strings (catches Pomi/Pomì).
        Name uses partial_ratio() (catches "Miscela Forte" vs "Miscela Forte Macinatura Moka").
        """
        name_b = normalize_for_comparison(incoming["name"])
        brand_b = normalize_for_comparison(incoming.get("brand") or "")

        for existing in candidates:
            brand_a = normalize_for_comparison(existing.get("brand") or "")

            if brand_a or brand_b:
                if not brand_a or not brand_b:
                    continue  # one branded, other not → different product
                brand_score = fuzz.ratio(brand_a, brand_b) / 100
                if brand_score < settings.product_brand_similarity_threshold:
                    continue

            name_a = normalize_for_comparison(existing["name"])
            name_score = fuzz.partial_ratio(name_a, name_b) / 100
            if name_score >= settings.product_name_similarity_threshold:
                return existing["id"]

        return None

    def _upsert_products_batch(
        self,
        sb: object,
        product_rows: list[dict],
    ) -> dict[tuple[str, str | None, str], str]:
        if not product_rows:
            return {}
        canonical_rows = [self._product_row_from_normalized(row) for row in product_rows]

        # --- Fuzzy pre-upsert deduplication against existing DB products ---
        # Group by format_key and fetch candidates once per unique format_key.
        # If a similar product already exists, reuse its id instead of inserting a duplicate.
        by_conflict_key: dict[tuple[str, str | None, str], str] = {}
        to_upsert: list[dict] = []
        candidates_cache: dict[str, list[dict]] = {}

        for row in canonical_rows:
            fk = row["format_key"]
            if fk not in candidates_cache:
                res = (
                    sb.table("products")  # type: ignore[union-attr]
                    .select("id, name, brand, format_key")
                    .eq("format_key", fk)
                    .execute()
                )
                candidates_cache[fk] = res.data or []

            existing_id = self._find_similar_product(row, candidates_cache[fk])
            key = self._conflict_key(row)
            if existing_id:
                by_conflict_key[key] = existing_id
            else:
                to_upsert.append(row)

        # --- Upsert only products with no fuzzy match ---
        if to_upsert:
            result = (
                sb.table("products")  # type: ignore[union-attr]
                .upsert(to_upsert, on_conflict="name,brand,format_key")
                .execute()
            )

            returned_rows = result.data or []
            if len(returned_rows) == len(to_upsert):
                for original, returned in zip(to_upsert, returned_rows, strict=False):
                    product_id = returned.get("id")
                    if product_id:
                        by_conflict_key[self._conflict_key(original)] = product_id
            else:
                for row in returned_rows:
                    if all(key in row for key in ("id", "name", "format_key")):
                        by_conflict_key[self._conflict_key(row)] = row["id"]

            if len(by_conflict_key) == len(canonical_rows):
                return by_conflict_key

            # Fallback: fetch by name for any still-missing products
            names = sorted({row["name"] for row in to_upsert if self._conflict_key(row) not in by_conflict_key})
            if names:
                existing = (
                    sb.table("products")
                    .select("id, name, brand, format_key")
                    .in_("name", names)
                    .execute()
                )
                for row in existing.data or []:
                    key = self._conflict_key(row)
                    if key not in by_conflict_key:
                        by_conflict_key[key] = row["id"]

        missing = [
            self._conflict_key(row)
            for row in canonical_rows
            if self._conflict_key(row) not in by_conflict_key
        ]
        if missing:
            raise ValueError(f"Product(s) not found after batch upsert: {missing!r}")
        return by_conflict_key

    def _build_offer_rows(
        self,
        product_ids: dict[tuple[str, str | None, str], str],
        normalized: list[dict],
        flyer: dict,
        supermarket_id: str | None,
        supermarket_name: str,
    ) -> list[dict]:
        offer_rows: list[dict] = []
        for p in normalized:
            key = self._conflict_key(p)
            product_id = product_ids[key]
            offer_rows.append(self._build_offer_row(product_id, p, flyer, supermarket_id, supermarket_name))
        return offer_rows

    def _format_size_metrics(self, expanded_products: list[dict]) -> tuple[float, float]:
        if not expanded_products:
            return (0.0, 0.0)

        compact_total = 0
        normalized_total = 0
        counted = 0
        for candidate in expanded_products:
            bundle = candidate.get("_format_bundle")
            if not isinstance(bundle, NormalizedFormatBundle):
                continue
            compact_total += json_size_bytes(bundle.format_compact)
            normalized_total += json_size_bytes(bundle.format_normalized)
            counted += 1

        if counted == 0:
            return (0.0, 0.0)
        return (compact_total / counted, normalized_total / counted)

    def _chunk_metadata(
        self,
        mime_type: str,
        pages_count: int,
        *,
        chunks_completed: int | None,
        chunk_failures: int,
    ) -> dict[str, int]:
        chunk_size = getattr(self._provider, "chunk_size_pages", 1) if mime_type == "application/pdf" else 1
        if not isinstance(chunk_size, int) or chunk_size < 1:
            chunk_size = 1
        chunks_total = max(1, (pages_count + chunk_size - 1) // chunk_size)
        return {
            "chunk_size_pages": chunk_size,
            "chunks_total": chunks_total,
            "chunks_completed": chunks_total if chunks_completed is None else chunks_completed,
            "chunk_failures": chunk_failures,
        }

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

        Uses ON CONFLICT (name, brand, format_key) DO UPDATE to always get the row
        back in the RETURNING clause, even when the product already existed.
        Falls back to a SELECT when the upsert returns an empty result set.

        Raises ValueError if the product cannot be found or created.
        """
        canonical_row = self._product_row_from_normalized(product_row)
        result = (
            sb.table("products")  # type: ignore[union-attr]
            .upsert(canonical_row, on_conflict="name,brand,format_key")
            .execute()
        )
        if result.data:
            return result.data[0]["id"]

        # Fallback: SELECT with conflict-key filters
        query = sb.table("products").select("id").eq("name", canonical_row["name"])  # type: ignore[union-attr]
        brand = canonical_row.get("brand")
        format_key = canonical_row.get("format_key")
        query = query.is_("brand", "null") if brand is None else query.eq("brand", brand)
        query = query.eq("format_key", format_key)
        existing = query.limit(1).execute()

        if not existing.data:
            raise ValueError(
                f"Product not found after upsert: name={canonical_row['name']!r} "
                f"brand={brand!r} format_key={format_key!r}"
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
        user_id: str | None = None,
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
        if user_id:
            notify_extraction_complete(
                sb,
                flyer_id=flyer_id,
                user_id=user_id,
                success=False,
                supermarket_name=supermarket_name,
                error_message=str(exc)[:100],
            )
