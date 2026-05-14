"""
FlyerCleanupService — nightly deletion of expired flyers.

Scheduled daily at midnight (Europe/Rome) via APScheduler in main.py lifespan.
Deletes: Supabase Storage file (best-effort) + DB row (CASCADE deletes all linked offers).
Flyers with valid_to IS NULL are never auto-deleted.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from core.config import settings
from core.database import get_supabase
from services.extraction.extraction_log import ERROR, INFO, log_event

logger = logging.getLogger(__name__)

_SELECT = "id, file_url, supermarket_name"
_STORAGE_URL_PREFIX = "{supabase_url}/storage/v1/object/public/flyers/"


class FlyerCleanupService:
    def __init__(
        self,
        supabase_factory: Callable[[], object] | None = None,
        today_factory: Callable[[], date] | None = None,
    ) -> None:
        self._supabase_factory = supabase_factory or get_supabase
        self._today_factory = today_factory or date.today

    def run(self) -> int:
        sb = self._supabase_factory()
        today = self._today_factory().isoformat()
        result = (
            sb.table("flyers")
            .select(_SELECT)
            .lt("valid_to", today)
            .neq("valid_to", None)
            .execute()
        )
        expired: list[dict] = result.data or []
        if not expired:
            logger.info("Flyer cleanup: no expired flyers for %s", today)
            return 0
        logger.info("Flyer cleanup: %d expired flyer(s) for %s", len(expired), today)
        deleted = sum(self._delete_one(sb, f) for f in expired)
        logger.info("Flyer cleanup complete: %d/%d deleted", deleted, len(expired))
        return deleted

    def _delete_one(self, sb: object, flyer: dict) -> int:
        flyer_id = flyer["id"]
        name = flyer.get("supermarket_name") or "?"
        storage_path = self._extract_storage_path(flyer.get("file_url") or "")
        if storage_path:
            self._delete_storage_file(sb, flyer_id, name, storage_path)
        log_event(sb, event_type=INFO, message=f"Deleting expired flyer: {name}", flyer_id=flyer_id, supermarket_name=name)
        try:
            sb.table("flyers").delete().eq("id", flyer_id).execute()
            logger.info("Deleted flyer %s (%s)", flyer_id, name)
            return 1
        except Exception as exc:
            logger.error("Failed to delete flyer row %s: %s", flyer_id, exc)
            log_event(sb, event_type=ERROR, message=f"Row delete failed: {exc!s:.200}", flyer_id=None, supermarket_name=name)
            return 0

    def _delete_storage_file(self, sb: object, flyer_id: str, name: str, path: str) -> None:
        try:
            sb.storage.from_("flyers").remove([path])
        except Exception as exc:
            logger.warning("Storage delete failed for %s (continuing): %s", flyer_id, exc)
            log_event(sb, event_type=ERROR, message=f"Storage delete failed (continuing): {exc!s:.200}", flyer_id=flyer_id, supermarket_name=name)

    def _extract_storage_path(self, file_url: str) -> str:
        prefix = _STORAGE_URL_PREFIX.format(supabase_url=settings.supabase_url.rstrip("/"))
        path = file_url.removeprefix(prefix)
        return path if path != file_url else ""
