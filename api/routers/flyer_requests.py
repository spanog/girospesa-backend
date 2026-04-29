from __future__ import annotations

import logging
from typing import Annotated

import resend
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.auth import get_optional_user_id
from core.config import settings
from core.database import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter()


class FlyerRequestBody(BaseModel):
    city: str = Field(..., min_length=1, max_length=200)
    supermarket: str | None = Field(None, max_length=200)
    flyer_url: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=500)
    email: str | None = Field(None, max_length=254)


class FlyerRequestResponse(BaseModel):
    id: str
    status: str


def _send_admin_notification(payload: FlyerRequestBody, user_id: str | None) -> None:
    """Send an email notification to the admin via Resend.

    Failures are logged but do NOT roll back the DB insert.
    """
    if not settings.resend_api_key or not settings.admin_notification_email:
        logger.warning("Resend not configured — skipping admin email notification")
        return

    resend.api_key = settings.resend_api_key

    html = f"""
    <h2>Nuova richiesta volantino</h2>
    <ul>
      <li><b>Città:</b> {payload.city}</li>
      <li><b>Supermercato:</b> {payload.supermarket or '—'}</li>
      <li><b>Link volantino:</b> {payload.flyer_url or '—'}</li>
      <li><b>Note:</b> {payload.notes or '—'}</li>
      <li><b>Email utente:</b> {payload.email or '—'}</li>
      <li><b>User ID:</b> {user_id or 'guest'}</li>
    </ul>
    """

    resend.Emails.send({
        "from": "notifiche@listaspesafurba.it",
        "to": settings.admin_notification_email,
        "subject": f"[LSF] Nuova richiesta volantino — {payload.city}",
        "html": html,
    })


@router.post("", status_code=status.HTTP_201_CREATED, response_model=FlyerRequestResponse)
async def create_flyer_request(
    body: FlyerRequestBody,
    user_id: Annotated[str | None, Depends(get_optional_user_id)],
) -> FlyerRequestResponse:
    """Submit a request for a supermarket flyer not yet covered by the app.

    No authentication required. If Resend fails, the DB record is preserved and
    ``{ id, status: 'pending' }`` is still returned to the caller.
    """
    sb = get_supabase()
    row = (
        sb.table("flyer_requests")
        .insert(
            {
                "city": body.city,
                "supermarket": body.supermarket,
                "flyer_url": body.flyer_url,
                "notes": body.notes,
                "email": body.email,
                "user_id": user_id,
                "status": "pending",
            }
        )
        .execute()
    )

    if not row.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save flyer request",
        )

    record = row.data[0]

    try:
        _send_admin_notification(body, user_id)
    except Exception:
        logger.exception("Resend notification failed for flyer request %s", record["id"])
        # Do not raise — DB record is the safety net

    return FlyerRequestResponse(id=record["id"], status=record["status"])
