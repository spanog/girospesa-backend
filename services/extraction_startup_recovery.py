"""Recover extraction jobs left in processing after a web-service restart."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from core.database import get_supabase

logger = logging.getLogger(__name__)

_SELECT = "id, file_name, supermarket_name, extraction_metadata"


class ExtractionStartupRecoveryService:
    def __init__(
        self,
        supabase_factory: Callable[[], object] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._supabase_factory = supabase_factory or get_supabase
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))

    def run(self) -> list[str]:
        sb = self._supabase_factory()
        result = sb.table("flyers").select(_SELECT).eq("status", "processing").execute()
        flyers: list[dict] = result.data or []
        if not flyers:
            logger.info("Extraction startup recovery: no processing flyers found")
            return []

        resumable_ids: list[str] = []
        for flyer in flyers:
            flyer_id = flyer["id"]
            metadata = flyer.get("extraction_metadata")
            if self._is_resumable(metadata):
                resumable_ids.append(flyer_id)
                self._mark_resumable(sb, flyer)
                continue
            self._mark_interrupted_without_checkpoint(sb, flyer)

        logger.info(
            "Extraction startup recovery: %d resumable, %d terminal",
            len(resumable_ids),
            len(flyers) - len(resumable_ids),
        )
        return resumable_ids

    def _is_resumable(self, metadata: object) -> bool:
        if not isinstance(metadata, dict):
            return False
        return bool(metadata.get("last_completed_chunk") and metadata.get("next_chunk_index"))

    def _mark_resumable(self, sb: object, flyer: dict) -> None:
        metadata = flyer.get("extraction_metadata")
        current = metadata.copy() if isinstance(metadata, dict) else {}
        current["resume_available"] = True
        current["extraction_finished_at"] = self._now_factory().isoformat().replace("+00:00", "Z")
        sb.table("flyers").update(  # type: ignore[union-attr]
            {
                "status": "error",
                "error_message": "Extraction interrupted by backend restart; automatic resume queued.",
                "extraction_metadata": current,
            }
        ).eq("id", flyer["id"]).execute()

    def _mark_interrupted_without_checkpoint(self, sb: object, flyer: dict) -> None:
        metadata = flyer.get("extraction_metadata")
        current = metadata.copy() if isinstance(metadata, dict) else {}
        current["resume_available"] = False
        current["extraction_finished_at"] = self._now_factory().isoformat().replace("+00:00", "Z")
        sb.table("flyers").update(  # type: ignore[union-attr]
            {
                "status": "error",
                "error_message": "Extraction interrupted by backend restart before a resumable checkpoint was saved.",
                "extraction_metadata": current,
            }
        ).eq("id", flyer["id"]).execute()

