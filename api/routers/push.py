"""Push notification endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.config import settings
from core.database import get_supabase
from services.push_notify import (
    notify_favorite_offer_published,
    notifications_enabled_for_user,
)

router = APIRouter()

_WEBHOOK_SECRET_HEADER = "x-webhook-secret"


# ── Request models ────────────────────────────────────────────────────────────


class SubscribeBody(BaseModel):
    endpoint: str
    p256dh: str
    auth_key: str
    user_agent: str | None = None


class UnsubscribeBody(BaseModel):
    endpoint: str


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/subscribe", status_code=201)
async def subscribe(
    body: SubscribeBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Register or update a Web Push subscription for the authenticated user."""
    sb = get_supabase()
    if not notifications_enabled_for_user(sb, user_id):
        raise HTTPException(
            status_code=409,
            detail="Riattiva le notifiche account prima di collegare un browser.",
        )
    # Remove any stale subscription with the same endpoint belonging to a different user.
    # This prevents cross-user notification leaks when a device switches accounts.
    sb.table("push_subscriptions").delete().eq("endpoint", body.endpoint).neq("user_id", user_id).execute()
    resp = (
        sb.table("push_subscriptions")
        .upsert(
            {
                "user_id": user_id,
                "endpoint": body.endpoint,
                "p256dh": body.p256dh,
                "auth_key": body.auth_key,
                "user_agent": body.user_agent,
            },
            on_conflict="user_id,endpoint",
        )
        .execute()
    )
    return resp.data[0]


@router.post("/unsubscribe", status_code=204)
async def unsubscribe(
    body: UnsubscribeBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    """Remove a specific Web Push subscription for the authenticated user."""
    sb = get_supabase()
    sb.table("push_subscriptions").delete().eq("user_id", user_id).eq("endpoint", body.endpoint).execute()
    return Response(status_code=204)


@router.delete("/subscriptions", status_code=204)
async def delete_all_subscriptions(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    """Remove all Web Push subscriptions for the authenticated user. Called on explicit logout."""
    sb = get_supabase()
    sb.table("push_subscriptions").delete().eq("user_id", user_id).execute()
    return Response(status_code=204)


@router.post("/notify-favorites", status_code=204)
async def notify_favorites(request: Request) -> Response:
    """
    Webhook called by a Supabase Database Webhook on INSERT in the offers table.
    Secured with the shared secret in the X-Webhook-Secret header.

    Payload format (Supabase webhook):
      { "type": "INSERT", "table": "offers", "record": { ...offer fields... } }
    """
    secret = request.headers.get(_WEBHOOK_SECRET_HEADER, "")
    if not settings.webhook_secret or secret != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = await request.json()
    record: dict = payload.get("record", {})
    notify_favorite_offer_published(get_supabase(), record)
    return Response(status_code=204)
