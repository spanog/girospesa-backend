"""Push notification endpoints."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.database import get_supabase

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────


class SubscribeBody(BaseModel):
    endpoint: str
    p256dh: str
    auth_key: str
    user_agent: str | None = None


class UnsubscribeBody(BaseModel):
    endpoint: str


class NativeSubscribeBody(BaseModel):
    token: str
    platform: str
    device_id: str | None = None
    user_agent: str | None = None


class NativeUnsubscribeBody(BaseModel):
    token: str


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


@router.post("/native/subscribe", status_code=201)
async def subscribe_native(
    body: NativeSubscribeBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Register or update a native FCM token for the authenticated user."""
    sb = get_supabase()
    sb.table("push_subscriptions").delete().eq("token", body.token).neq("user_id", user_id).execute()
    resp = (
        sb.table("push_subscriptions")
        .upsert(
            {
                "user_id": user_id,
                "channel": "native_fcm",
                "endpoint": f"fcm:{body.token}",
                "p256dh": "",
                "auth_key": "",
                "token": body.token,
                "platform": body.platform,
                "device_id": body.device_id,
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


@router.post("/native/unsubscribe", status_code=204)
async def unsubscribe_native(
    body: NativeUnsubscribeBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    """Remove a native FCM token for the authenticated user."""
    sb = get_supabase()
    (
        sb.table("push_subscriptions")
        .delete()
        .eq("user_id", user_id)
        .eq("channel", "native_fcm")
        .eq("token", body.token)
        .execute()
    )
    return Response(status_code=204)


@router.delete("/subscriptions", status_code=204)
async def delete_all_subscriptions(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    """Remove all Web Push subscriptions for the authenticated user. Called on explicit logout."""
    sb = get_supabase()
    sb.table("push_subscriptions").delete().eq("user_id", user_id).execute()
    return Response(status_code=204)
