"""
ExtractionService — on-demand AI extraction pipeline.

Runs inside the backend as a FastAPI BackgroundTask.

Pipeline:
  1. Fetch flyer row from DB
  2. Download file bytes
  3. Extract offer candidates via configured LLM provider
  4. Normalize + deduplicate
  5. Persist self-contained draft offers (is_confirmed=False)
  6. Attach each generated crop directly to its offer
  7. Update flyer status to 'done'

For PDFs, chunk results are persisted incrementally. If one chunk fails after
retries, previous chunk offers remain saved as draft offers and a later retry
resumes from the first failed chunk.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Callable

import requests

from core.config import settings
from core.database import get_supabase

from services.extraction.normalizer import (
    deduplicate_products,
    expand_products,
    json_size_bytes,
    normalize_product,
)
from services.extraction.pdf_utils import count_pdf_pages, is_pdf, mime_type_for_filename
from services.extraction.packshots import render_packshot
from services.extraction.providers import ExtractionProvider, get_provider
from services.extraction.providers.base import PdfChunkExtractionError
from services.extraction.extraction_log import ERROR, SUCCESS, WARNING, log_event
from services.product_format import NormalizedFormatBundle
from services.push_notify import notify_extraction_complete

logger = logging.getLogger(__name__)

_FLYER_SELECT = (
    "id, file_url, file_name, supermarket_id, supermarket_name, valid_from, valid_to, "
    "user_id, status, extraction_metadata"
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
        """Download, extract, normalize, and persist self-contained draft offers."""
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
        resume_state = self._resume_state(flyer, mime_type, pages_count)
        if mime_type == "application/pdf":
            self._provider.chunk_size_pages = resume_state["chunk_size_pages"]
        extraction_started_at = resume_state["extraction_started_at"]
        runtime = self._build_runtime_state(resume_state)
        self._save_pending_packshots(sb, flyer_id, content)

        sb.table("flyers").update(  # type: ignore[union-attr]
            {
                "extraction_metadata": self._extracting_metadata(
                    mime_type=mime_type,
                    pages_count=pages_count,
                    extraction_started_at=extraction_started_at,
                    progress=self._initial_progress_payload(
                        mime_type=mime_type,
                        pages_count=pages_count,
                        start_chunk_index=resume_state["start_chunk_index"],
                    ),
                    runtime=runtime,
                ),
            }
        ).eq("id", flyer_id).execute()

        logger.info(
            "  Sending to %s (%s, %d page(s))…",
            settings.llm_provider.upper(),
            mime_type,
            pages_count,
        )
        provider_started_at = time.perf_counter()
        all_products: list[dict] = []
        retry_errors: list[str] = []
        starting_saved_count = runtime["products_saved_count"]

        def _progress_callback(progress: dict) -> None:
            self._update_chunk_progress(
                sb,
                flyer_id,
                mime_type,
                pages_count,
                extraction_started_at,
                progress,
                runtime,
            )

        def _persist_chunk(chunk_payload: dict) -> None:
            chunk_products = chunk_payload["products"]
            chunk_retry_errors = list(chunk_payload.get("retry_errors") or [])
            saved_count = self._persist_products(
                sb,
                flyer=flyer,
                supermarket_id=supermarket_id,
                supermarket_name=supermarket_name,
                extracted_products=chunk_products,
                runtime=runtime,
            )
            self._checkpoint_persisted_chunk(
                sb=sb,
                flyer_id=flyer_id,
                mime_type=mime_type,
                pages_count=pages_count,
                extraction_started_at=extraction_started_at,
                chunk_payload=chunk_payload,
                runtime=runtime,
            )
            self._save_pending_packshots(sb, flyer_id, content)
            retry_errors.extend(chunk_retry_errors)
            if chunk_retry_errors:
                log_event(
                    sb,
                    event_type=WARNING,
                    message=f"{len(chunk_retry_errors)} retry attempt(s) before chunk result",
                    flyer_id=flyer_id,
                    supermarket_id=supermarket_id,
                    supermarket_name=supermarket_name,
                    details={
                        "chunk_index": chunk_payload["chunk_index"],
                        "current_chunk_start": chunk_payload["current_chunk_start"],
                        "current_chunk_end": chunk_payload["current_chunk_end"],
                        "retry_errors": chunk_retry_errors,
                        "saved_products_count": saved_count,
                    },
                )

        if mime_type == "application/pdf":
            all_products, provider_retry_errors = self._provider.extract_products(
                content,
                mime_type,
                progress_callback=_progress_callback,
                chunk_result_callback=_persist_chunk,
                start_chunk_index=resume_state["start_chunk_index"],
            )
            retry_errors.extend(provider_retry_errors)
            if runtime["products_saved_count"] == starting_saved_count and all_products:
                self._persist_products(
                    sb,
                    flyer=flyer,
                    supermarket_id=supermarket_id,
                    supermarket_name=supermarket_name,
                    extracted_products=all_products,
                    runtime=runtime,
                )
            runtime["chunks_completed"] = self._chunk_metadata(
                mime_type,
                pages_count,
                chunks_completed=None,
                chunk_failures=runtime["chunk_failures"],
            )["chunks_completed"]
        else:
            all_products, retry_errors = self._provider.extract_products(
                content,
                mime_type,
                progress_callback=_progress_callback,
            )
            self._persist_products(
                sb,
                flyer=flyer,
                supermarket_id=supermarket_id,
                supermarket_name=supermarket_name,
                extracted_products=all_products,
                runtime=runtime,
            )
        self._save_pending_packshots(sb, flyer_id, content)
        provider_seconds = time.perf_counter() - provider_started_at

        sb.table("flyers").update({  # type: ignore[union-attr]
            "extraction_metadata": {
                "stage": "saving",
                "pages_total": pages_count,
                "products_found": runtime["products_saved_count"],
                "provider_seconds": round(provider_seconds, 3),
                "extraction_started_at": extraction_started_at,
                **self._chunk_metadata(
                    mime_type,
                    pages_count,
                    chunks_completed=runtime["chunks_completed"],
                    chunk_failures=runtime["chunk_failures"],
                ),
                "last_completed_chunk": runtime["chunks_completed"],
                "partial_products_count": runtime["products_saved_count"],
            },
        }).eq("id", flyer_id).execute()

        if retry_errors and mime_type != "application/pdf":
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

        if runtime["products_saved_count"] == 0:
            raise ValueError("No valid products after normalization")

        saving_metadata = {
            "stage": "saving",
            "pages_total": pages_count,
            "products_found": runtime["products_saved_count"],
            "products_raw_count": runtime["products_raw_count"],
            "products_after_variants_count": runtime["products_after_variants_count"],
            "products_unique_count": runtime["products_unique_count"],
            "provider_seconds": round(provider_seconds, 3),
            "variant_expansion_seconds": round(runtime["variant_expansion_seconds"], 3),
            "normalization_seconds": round(runtime["normalization_seconds"], 3),
            "dedupe_seconds": round(runtime["dedupe_seconds"], 3),
            "avg_format_bytes_compact": round(self._average_metric(runtime["format_compact_total"], runtime["format_metrics_count"]), 2),
            "avg_format_bytes_normalized": round(self._average_metric(runtime["format_normalized_total"], runtime["format_metrics_count"]), 2),
            "extraction_started_at": extraction_started_at,
            **self._chunk_metadata(
                mime_type,
                pages_count,
                chunks_completed=runtime["chunks_completed"],
                chunk_failures=runtime["chunk_failures"],
            ),
            "last_completed_chunk": runtime["chunks_completed"],
            "partial_products_count": runtime["products_saved_count"],
        }
        sb.table("flyers").update({  # type: ignore[union-attr]
            "extraction_metadata": saving_metadata,
        }).eq("id", flyer_id).execute()

        total_seconds = time.time() - t_start
        elapsed = int(total_seconds)
        summary_metadata = {
            "extraction_started_at": extraction_started_at,
            "extraction_finished_at": self._utc_timestamp(),
            "provider_seconds": round(provider_seconds, 3),
            "variant_expansion_seconds": round(runtime["variant_expansion_seconds"], 3),
            "normalization_seconds": round(runtime["normalization_seconds"], 3),
            "dedupe_seconds": round(runtime["dedupe_seconds"], 3),
            "product_upsert_seconds": round(runtime["product_upsert_seconds"], 3),
            "offer_insert_seconds": round(runtime["offer_insert_seconds"], 3),
            "total_seconds": round(total_seconds, 3),
            "products_raw_count": runtime["products_raw_count"],
            "products_after_variants_count": runtime["products_after_variants_count"],
            "products_unique_count": runtime["products_unique_count"],
            "avg_format_bytes_compact": round(self._average_metric(runtime["format_compact_total"], runtime["format_metrics_count"]), 2),
            "avg_format_bytes_normalized": round(self._average_metric(runtime["format_normalized_total"], runtime["format_metrics_count"]), 2),
            **self._chunk_metadata(
                mime_type,
                pages_count,
                chunks_completed=runtime["chunks_completed"],
                chunk_failures=runtime["chunk_failures"],
            ),
            "last_completed_chunk": runtime["chunks_completed"],
            "partial_products_count": runtime["products_saved_count"],
            "resume_available": False,
        }
        sb.table("flyers").update({  # type: ignore[union-attr]
            "status": "done",
            "error_message": None,
            "products_count": runtime["products_saved_count"],
            "pages_count": pages_count,
            "extraction_metadata": summary_metadata,
        }).eq("id", flyer_id).execute()

        logger.info(
            "Done flyer %s (%s) — %d products in %ds [provider=%.3fs variants=%.3fs normalize=%.3fs dedupe=%.3fs upsert=%.3fs offers=%.3fs]",
            flyer_id,
            supermarket_name,
            runtime["products_saved_count"],
            elapsed,
            provider_seconds,
            runtime["variant_expansion_seconds"],
            runtime["normalization_seconds"],
            runtime["dedupe_seconds"],
            runtime["product_upsert_seconds"],
            runtime["offer_insert_seconds"],
        )
        log_event(
            sb,
            event_type=SUCCESS,
            message=f"Extraction completed: {runtime['products_saved_count']} products in {elapsed}s",
            flyer_id=flyer_id,
            supermarket_id=supermarket_id,
            supermarket_name=supermarket_name,
            details={
                "products_count": runtime["products_saved_count"],
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
                products_count=runtime["products_saved_count"],
            )

    def _resume_state(self, flyer: dict, mime_type: str, pages_count: int) -> dict:
        metadata = flyer.get("extraction_metadata")
        current = metadata if isinstance(metadata, dict) else {}
        default_chunk_size = getattr(self._provider, "chunk_size_pages", 1) if mime_type == "application/pdf" else 1
        saved_chunk_size = self._int_metadata(current.get("chunk_size_pages"), minimum=1)
        chunks_completed = self._int_metadata(current.get("last_completed_chunk"), minimum=0) or self._int_metadata(
            current.get("chunks_completed"),
            minimum=0,
        ) or 0
        chunk_size = saved_chunk_size if mime_type == "application/pdf" and chunks_completed > 0 and saved_chunk_size else default_chunk_size
        if not isinstance(chunk_size, int) or chunk_size < 1:
            chunk_size = 1
        chunks_total = max(1, (pages_count + chunk_size - 1) // chunk_size)
        next_chunk_index = self._int_metadata(current.get("next_chunk_index"), minimum=1)
        resume_available = (
            mime_type == "application/pdf"
            and next_chunk_index is not None
            and next_chunk_index <= chunks_total
            and (bool(current.get("resume_available")) or chunks_completed > 0)
        )
        start_chunk_index = next_chunk_index if resume_available and next_chunk_index is not None else 1
        if start_chunk_index is None or start_chunk_index > chunks_total:
            start_chunk_index = 1
            resume_available = False
        return {
            "resume_available": resume_available,
            "chunk_size_pages": chunk_size,
            "start_chunk_index": start_chunk_index,
            "extraction_started_at": current.get("extraction_started_at") if resume_available else self._utc_timestamp(),
            "products_saved_count": self._int_metadata(
                current.get("partial_products_count") or current.get("products_found"),
                minimum=0,
            )
            or 0,
            "products_raw_count": self._int_metadata(current.get("products_raw_count"), minimum=0) or 0,
            "products_after_variants_count": self._int_metadata(
                current.get("products_after_variants_count"),
                minimum=0,
            )
            or 0,
            "products_unique_count": self._int_metadata(current.get("products_unique_count"), minimum=0) or 0,
            "variant_expansion_seconds": self._float_metadata(current.get("variant_expansion_seconds")),
            "normalization_seconds": self._float_metadata(current.get("normalization_seconds")),
            "dedupe_seconds": self._float_metadata(current.get("dedupe_seconds")),
            "product_upsert_seconds": self._float_metadata(current.get("product_upsert_seconds")),
            "offer_insert_seconds": self._float_metadata(current.get("offer_insert_seconds")),
            "format_compact_total": self._float_metadata(
                current.get("avg_format_bytes_compact")
            )
            * (self._int_metadata(current.get("products_after_variants_count"), minimum=0) or 0),
            "format_normalized_total": self._float_metadata(
                current.get("avg_format_bytes_normalized")
            )
            * (self._int_metadata(current.get("products_after_variants_count"), minimum=0) or 0),
            "format_metrics_count": self._int_metadata(
                current.get("products_after_variants_count"),
                minimum=0,
            )
            or 0,
            "chunks_completed": chunks_completed,
            "chunk_failures": self._int_metadata(current.get("chunk_failures"), minimum=0) or 0,
        }

    def _build_runtime_state(self, resume_state: dict) -> dict:
        return {
            "products_saved_count": resume_state["products_saved_count"],
            "products_raw_count": resume_state["products_raw_count"],
            "products_after_variants_count": resume_state["products_after_variants_count"],
            "products_unique_count": resume_state["products_unique_count"],
            "variant_expansion_seconds": resume_state["variant_expansion_seconds"],
            "normalization_seconds": resume_state["normalization_seconds"],
            "dedupe_seconds": resume_state["dedupe_seconds"],
            "product_upsert_seconds": resume_state["product_upsert_seconds"],
            "offer_insert_seconds": resume_state["offer_insert_seconds"],
            "format_compact_total": resume_state["format_compact_total"],
            "format_normalized_total": resume_state["format_normalized_total"],
            "format_metrics_count": resume_state["format_metrics_count"],
            "chunks_completed": resume_state["chunks_completed"],
            "chunk_failures": resume_state["chunk_failures"],
        }

    def _persist_products(
        self,
        sb: object,
        *,
        flyer: dict,
        supermarket_id: str | None,
        supermarket_name: str,
        extracted_products: list[dict],
        runtime: dict,
    ) -> int:
        variant_started_at = time.perf_counter()
        expanded_products = expand_products(extracted_products)
        runtime["variant_expansion_seconds"] += time.perf_counter() - variant_started_at

        normalized_started_at = time.perf_counter()
        normalized = [
            normalize_product(candidate)
            for candidate in expanded_products
            if candidate.get("name") and (candidate.get("price_offer") or candidate.get("price_current"))
        ]
        runtime["normalization_seconds"] += time.perf_counter() - normalized_started_at

        dedupe_started_at = time.perf_counter()
        normalized = [p for p in normalized if p.get("name") and p.get("price_offer")]
        normalized = deduplicate_products(normalized)
        runtime["dedupe_seconds"] += time.perf_counter() - dedupe_started_at

        runtime["products_raw_count"] += len(extracted_products)
        runtime["products_after_variants_count"] += len(expanded_products)
        runtime["products_unique_count"] += len(normalized)

        compact_total, normalized_total, counted = self._format_metric_totals(expanded_products)
        runtime["format_compact_total"] += compact_total
        runtime["format_normalized_total"] += normalized_total
        runtime["format_metrics_count"] += counted

        if not normalized:
            return 0

        offer_insert_started_at = time.perf_counter()
        offer_rows = self._build_offer_rows(
            normalized,
            flyer,
            supermarket_id,
            supermarket_name,
        )
        unique_offer_rows = self._deduplicate_offer_rows(offer_rows)
        if unique_offer_rows:
            sb.table("offers").upsert(  # type: ignore[union-attr]
                unique_offer_rows,
                on_conflict="flyer_id,offer_key,format_key",
                ignore_duplicates=True,
            ).execute()
        runtime["offer_insert_seconds"] += time.perf_counter() - offer_insert_started_at
        runtime["products_saved_count"] += len(unique_offer_rows)
        return len(unique_offer_rows)

    def _save_pending_packshots(self, sb: object, flyer_id: str, pdf_bytes: bytes) -> None:
        pending = (
            sb.table("offers")  # type: ignore[union-attr]
            .select("id, image_url, packshot_source_page, packshot_bbox")
            .eq("flyer_id", flyer_id)
            .is_("image_url", "null")
            .not_.is_("packshot_source_page", "null")
            .not_.is_("packshot_bbox", "null")
            .execute()
        )
        for offer in pending.data or []:
            self._upload_packshot(sb, offer, pdf_bytes, offer["packshot_source_page"], offer["packshot_bbox"])

    def _upload_packshot(
        self, sb: object, offer: dict, pdf_bytes: bytes, source_page: int, packshot_bbox: object
    ) -> None:
        image = render_packshot(pdf_bytes, source_page, packshot_bbox)
        if not image:
            return
        try:
            path = f"draft-offers/{offer['id']}/auto-packshot.png"
            sb.storage.from_("product-images").upload(
                path=path, file=image, file_options={"content-type": "image/png", "upsert": "true"}
            )
            url = sb.storage.from_("product-images").get_public_url(path)
            sb.table("offers").update({"image_url": url}).eq("id", offer["id"]).execute()
        finally:
            del image

    def _checkpoint_persisted_chunk(
        self,
        *,
        sb: object,
        flyer_id: str,
        mime_type: str,
        pages_count: int,
        extraction_started_at: str,
        chunk_payload: dict,
        runtime: dict,
    ) -> None:
        self._update_chunk_progress(
            sb,
            flyer_id,
            mime_type,
            pages_count,
            extraction_started_at,
            {
                "chunks_completed": chunk_payload["chunk_index"],
                "current_chunk_start": chunk_payload["current_chunk_start"],
                "current_chunk_end": chunk_payload["current_chunk_end"],
                "pages_processed": chunk_payload["current_chunk_end"],
                "products_found": runtime["products_saved_count"],
            },
            runtime,
        )

    def _conflict_key(self, row: dict) -> tuple[str, str | None]:
        return (row["name"], row.get("brand"))

    def _offer_key(self, row: dict) -> str:
        name = " ".join((row.get("name") or "").split()).strip().lower()
        brand = " ".join((row.get("brand") or "").split()).strip().lower()
        return f"{name}|{brand}"

    def _build_offer_rows(
        self,
        normalized: list[dict],
        flyer: dict,
        supermarket_id: str | None,
        supermarket_name: str,
    ) -> list[dict]:
        offer_rows: list[dict] = []
        for p in normalized:
            offer_rows.append(self._build_offer_row(p, flyer, supermarket_id, supermarket_name))
        return offer_rows

    def _deduplicate_offer_rows(self, offer_rows: list[dict]) -> list[dict]:
        seen: set[tuple[str | None, str | None, str]] = set()
        unique: list[dict] = []
        for row in offer_rows:
            key = (row.get("flyer_id"), row.get("offer_key"), row["format_key"])
            if key not in seen:
                seen.add(key)
                unique.append(row)
        return unique

    def _format_metric_totals(self, expanded_products: list[dict]) -> tuple[float, float, int]:
        compact_total = 0.0
        normalized_total = 0.0
        counted = 0
        for candidate in expanded_products:
            bundle = candidate.get("_format_bundle")
            if not isinstance(bundle, NormalizedFormatBundle):
                continue
            compact_total += json_size_bytes(bundle.format_compact)
            normalized_total += json_size_bytes(bundle.format_normalized)
            counted += 1
        return compact_total, normalized_total, counted

    def _average_metric(self, total: float, counted: int) -> float:
        if counted <= 0:
            return 0.0
        return total / counted

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

    def _progress_percent(self, pages_processed: int, pages_count: int) -> int:
        if pages_count < 1:
            return 0
        return min(100, round((pages_processed / pages_count) * 100))

    def _utc_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _initial_progress_payload(
        self,
        *,
        mime_type: str,
        pages_count: int,
        start_chunk_index: int,
    ) -> dict:
        chunk_size = getattr(self._provider, "chunk_size_pages", 1) if mime_type == "application/pdf" else 1
        if not isinstance(chunk_size, int) or chunk_size < 1:
            chunk_size = 1
        current_chunk_start = ((start_chunk_index - 1) * chunk_size) + 1
        current_chunk_end = min(current_chunk_start + chunk_size - 1, pages_count)
        pages_processed = max(0, current_chunk_start - 1)
        return {
            "current_chunk_start": current_chunk_start,
            "current_chunk_end": current_chunk_end,
            "pages_processed": pages_processed,
            "chunks_completed": max(0, start_chunk_index - 1),
            "progress_percent": 5 if pages_processed == 0 else self._progress_percent(pages_processed, pages_count),
        }

    def _extracting_metadata(
        self,
        *,
        mime_type: str,
        pages_count: int,
        extraction_started_at: str,
        progress: dict,
        runtime: dict,
    ) -> dict:
        pages_processed = int(progress.get("pages_processed") or 0)
        chunks_completed = int(progress.get("chunks_completed") or 0)
        progress_percent = progress.get("progress_percent")
        if not isinstance(progress_percent, int):
            progress_percent = self._progress_percent(pages_processed, pages_count)
        products_found = max(
            runtime["products_saved_count"],
            int(progress.get("products_found") or 0),
        )
        next_chunk_index = chunks_completed + 1
        chunk_size = self._chunk_metadata(mime_type, pages_count, chunks_completed=0, chunk_failures=0)["chunk_size_pages"]
        if next_chunk_index > self._chunk_metadata(mime_type, pages_count, chunks_completed=0, chunk_failures=0)["chunks_total"]:
            next_chunk_index = None
            next_chunk_start = None
            next_chunk_end = None
        else:
            next_chunk_start = ((next_chunk_index - 1) * chunk_size) + 1
            next_chunk_end = min(next_chunk_start + chunk_size - 1, pages_count)
        return {
            "stage": "extracting",
            "pages_total": pages_count,
            "extraction_started_at": extraction_started_at,
            "progress_percent": progress_percent,
            **self._chunk_metadata(
                mime_type,
                pages_count,
                chunks_completed=chunks_completed,
                chunk_failures=runtime["chunk_failures"],
            ),
            "current_chunk_start": progress.get("current_chunk_start"),
            "current_chunk_end": progress.get("current_chunk_end"),
            "pages_processed": pages_processed,
            "products_found": products_found,
            "partial_products_count": runtime["products_saved_count"],
            "last_completed_chunk": chunks_completed,
            "next_chunk_index": next_chunk_index,
            "next_chunk_start": next_chunk_start,
            "next_chunk_end": next_chunk_end,
            "resume_available": False,
            "products_raw_count": runtime["products_raw_count"],
            "products_after_variants_count": runtime["products_after_variants_count"],
            "products_unique_count": runtime["products_unique_count"],
            "variant_expansion_seconds": round(runtime["variant_expansion_seconds"], 3),
            "normalization_seconds": round(runtime["normalization_seconds"], 3),
            "dedupe_seconds": round(runtime["dedupe_seconds"], 3),
            "product_upsert_seconds": round(runtime["product_upsert_seconds"], 3),
            "offer_insert_seconds": round(runtime["offer_insert_seconds"], 3),
            "avg_format_bytes_compact": round(
                self._average_metric(runtime["format_compact_total"], runtime["format_metrics_count"]),
                2,
            ),
            "avg_format_bytes_normalized": round(
                self._average_metric(runtime["format_normalized_total"], runtime["format_metrics_count"]),
                2,
            ),
        }

    def _update_chunk_progress(
        self,
        sb: object,
        flyer_id: str,
        mime_type: str,
        pages_count: int,
        extraction_started_at: str,
        progress: dict,
        runtime: dict,
    ) -> None:
        chunks_completed = int(progress.get("chunks_completed") or 0)
        if chunks_completed > runtime["chunks_completed"]:
            runtime["chunks_completed"] = chunks_completed
        metadata = self._extracting_metadata(
            mime_type=mime_type,
            pages_count=pages_count,
            extraction_started_at=extraction_started_at,
            progress=progress,
            runtime=runtime,
        )
        sb.table("flyers").update({"extraction_metadata": metadata}).eq("id", flyer_id).execute()  # type: ignore[union-attr]

    def _error_metadata(self, sb: object, flyer_id: str, exc: Exception) -> dict | None:
        result = (
            sb.table("flyers")  # type: ignore[union-attr]
            .select("file_name, extraction_metadata")
            .eq("id", flyer_id)
            .maybe_single()
            .execute()
        )
        flyer = result.data if result and result.data else None
        current = flyer.get("extraction_metadata") if isinstance(flyer, dict) else None
        if not isinstance(current, dict):
            return None
        metadata = {
            **current,
            "extraction_finished_at": self._utc_timestamp(),
        }
        if isinstance(exc, PdfChunkExtractionError):
            metadata["resume_available"] = True
            metadata["failed_chunk_index"] = exc.chunk_index
            metadata["failed_chunk_start"] = exc.start_page
            metadata["failed_chunk_end"] = exc.end_page
            metadata["next_chunk_index"] = exc.chunk_index
            metadata["next_chunk_start"] = exc.start_page
            metadata["next_chunk_end"] = exc.end_page
            metadata["chunk_failures"] = int(current.get("chunk_failures") or 0) + 1
            metadata["partial_products_count"] = int(
                current.get("partial_products_count")
                or current.get("products_found")
                or 0
            )
            return metadata
        if self._should_resume_after_error(current, flyer.get("file_name") if isinstance(flyer, dict) else None):
            metadata["resume_available"] = True
            metadata["partial_products_count"] = int(
                current.get("partial_products_count")
                or current.get("products_found")
                or 0
            )
            metadata.pop("failed_chunk_index", None)
            metadata.pop("failed_chunk_start", None)
            metadata.pop("failed_chunk_end", None)
        return metadata

    def _error_file_name(self, sb: object, flyer_id: str) -> str | None:
        result = (
            sb.table("flyers")  # type: ignore[union-attr]
            .select("file_name")
            .eq("id", flyer_id)
            .maybe_single()
            .execute()
        )
        row = result.data if result and result.data else None
        if not isinstance(row, dict):
            return None
        file_name = row.get("file_name")
        return file_name if isinstance(file_name, str) else None

    def _should_finalize_after_error(self, metadata: dict | None, file_name: str | None) -> bool:
        if not isinstance(metadata, dict):
            return False
        partial_products_count = self._int_metadata(
            metadata.get("partial_products_count") or metadata.get("products_found"),
            minimum=1,
        )
        if partial_products_count is None:
            return False
        if is_pdf(file_name or ""):
            chunks_total = self._int_metadata(metadata.get("chunks_total"), minimum=1)
            chunks_completed = self._int_metadata(
                metadata.get("last_completed_chunk"),
                minimum=0,
            ) or self._int_metadata(metadata.get("chunks_completed"), minimum=0)
            return bool(
                chunks_total is not None
                and chunks_completed is not None
                and chunks_completed >= chunks_total
            )
        return metadata.get("stage") == "saving"

    def _should_resume_after_error(self, metadata: dict, file_name: str | None) -> bool:
        if not is_pdf(file_name or ""):
            return False
        chunks_total = self._int_metadata(metadata.get("chunks_total"), minimum=1)
        next_chunk_index = self._int_metadata(metadata.get("next_chunk_index"), minimum=1)
        chunks_completed = self._int_metadata(metadata.get("last_completed_chunk"), minimum=0) or self._int_metadata(
            metadata.get("chunks_completed"),
            minimum=0,
        ) or 0
        return bool(
            chunks_total is not None
            and next_chunk_index is not None
            and chunks_completed > 0
            and next_chunk_index <= chunks_total
        )

    def _int_metadata(self, value: object, *, minimum: int) -> int | None:
        if isinstance(value, int) and value >= minimum:
            return value
        return None

    def _float_metadata(self, value: object) -> float:
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return 0.0

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

    def _build_offer_row(
        self,
        p: dict,
        flyer: dict,
        supermarket_id: str | None,
        supermarket_name: str,
    ) -> dict:
        return {
            "name": p.get("name"),
            "brand": p.get("brand"),
            "category": p.get("category"),
            "subcategory": p.get("subcategory"),
            "offer_key": self._offer_key(p),
            "supermarket_id": supermarket_id,
            "supermarket_name": supermarket_name,
            "flyer_id": flyer["id"],
            "format": p.get("format", {}),
            "format_key": p.get("format_key", "v1:{}"),
            "format_label": p.get("format_label", ""),
            "price_offer": p["price_offer"],
            "price_original": p.get("price_original"),
            "unit_price": p.get("unit_price"),
            "unit_price_value": p.get("unit_price_value"),
            "unit_price_unit": p.get("unit_price_unit"),
            "offer_notes": p.get("offer_notes"),
            "valid_from": flyer.get("valid_from") or p.get("valid_from"),
            "valid_to": flyer.get("valid_to") or p.get("valid_to"),
            "is_confirmed": False,
            "packshot_source_page": p.get("source_page"),
            "packshot_bbox": p.get("packshot_bbox"),
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
        error_metadata = self._error_metadata(sb, flyer_id, exc)
        file_name = self._error_file_name(sb, flyer_id)
        finalized_after_error = self._should_finalize_after_error(error_metadata, file_name)
        if finalized_after_error:
            done_update = {
                "status": "done",
                "error_message": None,
                "products_count": int(
                    error_metadata.get("partial_products_count")
                    or error_metadata.get("products_found")
                    or 0
                ),
            }
            pages_total = self._int_metadata(error_metadata.get("pages_total"), minimum=1)
            if pages_total is not None:
                done_update["pages_count"] = pages_total
            done_update["extraction_metadata"] = {
                **error_metadata,
                "resume_available": False,
            }
            sb.table("flyers").update(done_update).eq("id", flyer_id).execute()  # type: ignore[union-attr]
        else:
            error_update = {
                "status": "error",
                "error_message": str(exc)[:500],
            }
            if error_metadata is not None:
                error_update["extraction_metadata"] = error_metadata
            sb.table("flyers").update(error_update).eq("id", flyer_id).execute()  # type: ignore[union-attr]

        details = {"error": str(exc), "elapsed_seconds": elapsed}
        resumable = False
        if isinstance(exc, PdfChunkExtractionError):
            resumable = True
            details["chunk"] = {
                "chunk_index": exc.chunk_index,
                "chunks_total": exc.chunks_total,
                "start_page": exc.start_page,
                "end_page": exc.end_page,
            }
            details["retry_errors"] = exc.retry_errors
        elif error_metadata is not None:
            resumable = bool(error_metadata.get("resume_available"))
        details["resume"] = {
            "available": False if finalized_after_error else resumable,
            "reason": (
                "completed_persistence_after_late_runtime_failure"
                if finalized_after_error
                else
                "provider_chunk_failure"
                if isinstance(exc, PdfChunkExtractionError)
                else "generic_runtime_failure_after_persisted_chunk"
                if resumable
                else "not_available"
            ),
            "next_chunk_index": error_metadata.get("next_chunk_index") if error_metadata else None,
            "last_completed_chunk": error_metadata.get("last_completed_chunk") if error_metadata else None,
            "partial_products_count": error_metadata.get("partial_products_count") if error_metadata else None,
        }
        log_event(
            sb,
            event_type=ERROR,
            message=f"Extraction failed after {elapsed}s: {exc!s:.500}",
            flyer_id=flyer_id,
            supermarket_id=supermarket_id,
            supermarket_name=supermarket_name,
            details=details,
        )
        if user_id:
            notify_extraction_complete(
                sb,
                flyer_id=flyer_id,
                user_id=user_id,
                success=finalized_after_error,
                supermarket_name=supermarket_name,
                products_count=int(
                    error_metadata.get("partial_products_count")
                    or error_metadata.get("products_found")
                    or 0
                )
                if finalized_after_error and error_metadata is not None
                else 0,
                error_message="" if finalized_after_error else str(exc)[:100],
            )
