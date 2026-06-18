from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, status

from core.config import settings
from services.contact_requests import (
    ContactRequestConfigurationError,
    SmtpProbeResponse,
    SmtpProbeService,
)
from services.flyer_cleanup import FlyerCleanupService
from services.purchased_items_cleanup import PurchasedItemsCleanupService

router = APIRouter()


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


@router.post("/cron/daily-maintenance", status_code=status.HTTP_200_OK)
async def trigger_daily_maintenance(
    x_ops_secret: str | None = Header(default=None),
) -> dict[str, int | str]:
    _require_ops_secret(x_ops_secret)
    deleted_offers = FlyerCleanupService().run()
    removed_purchased_items = PurchasedItemsCleanupService().run()
    return {
        "status": "ok",
        "deleted_offers": deleted_offers,
        "removed_purchased_items": removed_purchased_items,
    }


@router.get("/smtp-probe", response_model=SmtpProbeResponse, status_code=status.HTTP_200_OK)
async def probe_smtp(
    x_ops_secret: str | None = Header(default=None),
    timeout_seconds: int = Query(default=10, ge=1, le=30),
) -> SmtpProbeResponse:
    _require_ops_secret(x_ops_secret)
    try:
        return SmtpProbeService().run(timeout_seconds=timeout_seconds)
    except ContactRequestConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
