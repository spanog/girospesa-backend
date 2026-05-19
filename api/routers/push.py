"""Push notification endpoints.

- POST   /push/subscribe     — register a Web Push subscription (auth required)
- POST   /push/unsubscribe   — remove a specific Web Push subscription (auth required)
- DELETE /push/subscriptions — remove all Web Push subscriptions for caller (auth required, called on logout)
- POST   /push/notify-favorites — Supabase DB webhook: called on INSERT in offers
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.config import settings
from core.database import get_supabase
from services.push_notify import PushEndpointGoneError, PushSubscription, send_push_notification

logger = logging.getLogger(__name__)
router = APIRouter()

_WEBHOOK_SECRET_HEADER = "x-webhook-secret"


def _offer_is_currently_active(record: dict) -> bool:
    today = date.today().isoformat()
    valid_from = record.get("valid_from")
    valid_to = record.get("valid_to")
    if valid_from and valid_from > today:
        return False
    if valid_to and valid_to < today:
        return False
    return True


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
    product_id: str | None = record.get("product_id")

    if not product_id:
        return Response(status_code=204)
    if not record.get("is_confirmed"):
        return Response(status_code=204)
    if not _offer_is_currently_active(record):
        return Response(status_code=204)

    sb = get_supabase()
    flyer_id: str | None = record.get("flyer_id")
    if not flyer_id:
        return Response(status_code=204)
    flyer_resp = (
        sb.table("flyers")
        .select("is_public, status")
        .eq("id", flyer_id)
        .maybe_single()
        .execute()
    )
    flyer = flyer_resp.data if flyer_resp else None
    if not flyer or not flyer.get("is_public") or flyer.get("status") != "done":
        return Response(status_code=204)

    # Resolve product name
    product_resp = sb.table("products").select("name").eq("id", product_id).maybe_single().execute()
    product_name: str = product_resp.data["name"] if product_resp.data else "prodotto"

    # Resolve supermarket name
    supermarket_id: str | None = record.get("supermarket_id")
    sm_name = ""
    if supermarket_id:
        sm_resp = sb.table("supermarkets").select("name").eq("id", supermarket_id).maybe_single().execute()
        sm_name = sm_resp.data["name"] if sm_resp.data else ""

    # Build notification body
    price = record.get("discounted_price") or record.get("original_price")
    valid_to: str = record.get("valid_to", "")
    parts = [
        f"€{price:.2f}" if price else "",
        f"da {sm_name}" if sm_name else "",
        f"Valida fino al {valid_to}" if valid_to else "",
    ]
    notification_body = " — ".join(p for p in parts if p)

    # Find users who favourited this product and have push notifications enabled
    favs_resp = (
        sb.table("favorites")
        .select("user_id")
        .eq("product_id", product_id)
        .execute()
    )

    stale_endpoints: list[tuple[str, str]] = []  # (user_id, endpoint)

    for fav in favs_resp.data:
        uid: str = fav["user_id"]

        # Check per-user notification preference
        profile_resp = (
            sb.table("user_profiles")
            .select("notification_favorites")
            .eq("id", uid)
            .maybe_single()
            .execute()
        )
        if not profile_resp.data or not profile_resp.data.get("notification_favorites", True):
            continue

        # Fetch all push subscriptions for this user
        subs_resp = (
            sb.table("push_subscriptions")
            .select("endpoint, p256dh, auth_key")
            .eq("user_id", uid)
            .execute()
        )

        for sub in subs_resp.data:
            subscription = PushSubscription(
                endpoint=sub["endpoint"],
                p256dh=sub["p256dh"],
                auth_key=sub["auth_key"],
            )
            try:
                send_push_notification(
                    subscription=subscription,
                    title=f"Nuova offerta: {product_name}",
                    body=notification_body,
                    data={"kind": "favorite_offer", "url": f"/offerte?product={product_id}", "product_id": product_id},
                )
            except PushEndpointGoneError:
                stale_endpoints.append((uid, sub["endpoint"]))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Push delivery failed for user %s: %s", uid, exc)

    # Remove stale subscriptions (endpoint returned 410 Gone)
    for uid, endpoint in stale_endpoints:
        sb.table("push_subscriptions").delete().eq("user_id", uid).eq("endpoint", endpoint).execute()

    return Response(status_code=204)
