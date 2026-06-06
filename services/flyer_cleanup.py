"""
FlyerCleanupService — nightly removal of offers from expired flyers.

Scheduled daily at midnight (Europe/Rome) via APScheduler in main.py lifespan.
Keeps expired flyer rows and files for admin history, but deletes linked offers so
customer-facing deal data stays clean. Flyers with valid_to IS NULL are ignored.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from core.database import get_supabase
from services.extraction.extraction_log import ERROR, INFO, log_event

logger = logging.getLogger(__name__)

_SELECT = "id, supermarket_name"


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
        deleted = sum(self._delete_offers_for_flyer(sb, flyer) for flyer in expired)
        logger.info(
            "Flyer cleanup complete: %d offer(s) deleted across %d expired flyer(s)",
            deleted,
            len(expired),
        )
        return deleted

    def _delete_offers_for_flyer(self, sb: object, flyer: dict) -> int:
        flyer_id = flyer["id"]
        name = flyer.get("supermarket_name") or "?"
        count = self._count_offers(sb, flyer_id)
        if count <= 0:
            logger.info("No offers to delete for expired flyer %s (%s)", flyer_id, name)
            return 0
        log_event(
            sb,
            event_type=INFO,
            message=f"Deleting {count} expired offer(s) for flyer: {name}",
            flyer_id=flyer_id,
            supermarket_name=name,
        )
        try:
            sb.table("offers").delete().eq("flyer_id", flyer_id).execute()
            logger.info("Deleted %d offer(s) for expired flyer %s (%s)", count, flyer_id, name)
            return count
        except Exception as exc:
            logger.error("Failed to delete offers for flyer %s: %s", flyer_id, exc)
            log_event(
                sb,
                event_type=ERROR,
                message=f"Offer delete failed: {exc!s:.200}",
                flyer_id=flyer_id,
                supermarket_name=name,
            )
            return 0

    def _count_offers(self, sb: object, flyer_id: str) -> int:
        result = sb.table("offers").select("id", count="exact").eq("flyer_id", flyer_id).execute()
        return result.count or 0
