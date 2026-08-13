from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, status

from core.config import settings
from services.flyer_cleanup import FlyerCleanupService
from services.notification_jobs import NotificationJobWorker
from services.purchased_items_cleanup import PurchasedItemsCleanupService

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_ops_secret(x_ops_secret: str | None) -> None:
    expected = settings.ops_cron_secret.strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ops cron secret is not configured",
        )
    if x_ops_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid ops secret",
        )


def _run_cleanup_step(name: str, runner: Callable[[], int]) -> tuple[int, str | None]:
    try:
        return runner(), None
    except Exception:
        logger.exception("Daily maintenance step failed: %s", name)
        return 0, name


@router.post("/cron/daily-maintenance", status_code=status.HTTP_200_OK)
async def trigger_daily_maintenance(
    x_ops_secret: str | None = Header(default=None),
) -> dict[str, int | str | list[str]]:
    _require_ops_secret(x_ops_secret)
    deleted_offers, flyer_error = _run_cleanup_step("flyer_cleanup", FlyerCleanupService().run)
    removed_purchased_items, purchased_error = _run_cleanup_step(
        "purchased_items_cleanup",
        PurchasedItemsCleanupService().run,
    )
    errors = [error for error in (flyer_error, purchased_error) if error]
    return {
        "status": "ok" if not errors else "partial_error",
        "deleted_offers": deleted_offers,
        "removed_purchased_items": removed_purchased_items,
        "errors": errors,
    }


@router.post("/cron/notifications", status_code=status.HTTP_200_OK)
async def trigger_notification_jobs(
    x_ops_secret: str | None = Header(default=None),
) -> dict[str, int | str]:
    _require_ops_secret(x_ops_secret)
    result = await asyncio.to_thread(NotificationJobWorker().run_pending)
    return {"status": "ok", **result}
