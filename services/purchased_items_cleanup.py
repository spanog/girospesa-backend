"""PurchasedItemsCleanupService — nightly removal of stale purchased list items."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

from core.database import get_supabase

logger = logging.getLogger(__name__)

_ROME_TZ = ZoneInfo("Europe/Rome")
_SELECT = "id, items"


class PurchasedItemsCleanupService:
    def __init__(
        self,
        supabase_factory: Callable[[], object] | None = None,
        today_factory: Callable[[], date] | None = None,
    ) -> None:
        self._supabase_factory = supabase_factory or get_supabase
        self._today_factory = today_factory or self._rome_today

    def run(self) -> int:
        sb = self._supabase_factory()
        today = self._today_factory()
        shopping_lists = self._load_lists(sb)
        removed = 0
        for shopping_list in shopping_lists:
            removed += self._cleanup_list(sb, shopping_list, today)
        logger.info("Purchased items cleanup removed %d item(s)", removed)
        return removed

    def _load_lists(self, sb: object) -> list[dict]:
        result = sb.table("shopping_lists").select(_SELECT).execute()  # type: ignore[union-attr,attr-defined]
        return result.data or []

    def _cleanup_list(self, sb: object, shopping_list: dict, today: date) -> int:
        items = shopping_list.get("items") or []
        kept = [item for item in items if self._keep_item(item, today)]
        removed = len(items) - len(kept)
        if removed == 0:
            return 0
        self._save_items(sb, shopping_list["id"], kept)
        return removed

    def _save_items(self, sb: object, list_id: str, items: list[dict]) -> None:
        sb.table("shopping_lists").update({"items": items}).eq("id", list_id).execute()  # type: ignore[union-attr,attr-defined]

    def _keep_item(self, item: dict, today: date) -> bool:
        if not item.get("purchased"):
            return True
        purchased_at = item.get("purchased_at")
        if not purchased_at:
            return True
        return self._purchased_on_or_after(purchased_at, today)

    def _purchased_on_or_after(self, purchased_at: str, today: date) -> bool:
        purchased_day = self._parse_timestamp(purchased_at).astimezone(_ROME_TZ).date()
        return purchased_day >= today

    def _parse_timestamp(self, value: str) -> datetime:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)

    def _rome_today(self) -> date:
        return datetime.now(_ROME_TZ).date()
