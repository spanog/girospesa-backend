"""Web Push notification service using pywebpush."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]

from core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushSubscription:
    endpoint: str
    p256dh: str
    auth_key: str


class PushEndpointGoneError(Exception):
    """Raised when a push endpoint returns 410 Gone (subscription revoked by browser)."""


def send_push_notification(
    subscription: PushSubscription,
    title: str,
    body: str,
    icon: str = "/favicon.ico",
    data: dict | None = None,
) -> None:
    """
    Send a single Web Push notification.

    Raises:
        PushEndpointGoneError: if the endpoint returned HTTP 410 — caller should
            delete the subscription from the database.
        WebPushException: for all other push delivery errors.
    """
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "icon": icon,
            "data": data or {},
        }
    )

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth": subscription.auth_key,
                },
            },
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_mailto},
        )
    except WebPushException as exc:
        if exc.response is not None and exc.response.status_code == 410:
            raise PushEndpointGoneError(subscription.endpoint) from exc
        raise


def notify_extraction_complete(
    sb: object,
    flyer_id: str,
    user_id: str,
    success: bool,
    supermarket_name: str,
    products_count: int = 0,
    error_message: str = "",
) -> None:
    """Persist inbox notification and send Web Push to the flyer uploader when extraction finishes."""
    try:
        profile_resp = (
            sb.table("user_profiles")  # type: ignore[union-attr]
            .select("notification_deals")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        if profile_resp.data is not None and not profile_resp.data.get("notification_deals", True):
            return
    except Exception as exc:
        logger.warning("Failed to fetch notification_deals pref for user %s: %s", user_id, exc)

    if success:
        title = "Estrazione completata"
        body = f"{products_count} prodotti estratti da {supermarket_name}"
        kind = "extraction_complete"
        status = "done"
    else:
        title = "Estrazione fallita"
        body = f"{supermarket_name}: {error_message}" if error_message else supermarket_name
        kind = "extraction_failed"
        status = "error"

    data = {
        "kind": kind,
        "flyer_id": flyer_id,
        "status": status,
        "products_count": products_count,
        "url": f"/admin/volantini/{flyer_id}",
    }

    try:
        sb.table("app_notifications").insert(  # type: ignore[union-attr]
            {
                "user_id": user_id,
                "kind": kind,
                "title": title,
                "body": body,
                "data": data,
            }
        ).execute()
    except Exception as exc:
        logger.warning("Failed to persist extraction app_notification for user %s: %s", user_id, exc)

    try:
        result = sb.table("push_subscriptions").select("*").eq("user_id", user_id).execute()  # type: ignore[union-attr]
        subscriptions = result.data or []
    except Exception as exc:
        logger.warning("Failed to fetch push subscriptions for user %s: %s", user_id, exc)
        return

    if not subscriptions:
        return

    stale_endpoints: list[str] = []
    for sub in subscriptions:
        try:
            send_push_notification(
                subscription=PushSubscription(
                    endpoint=sub["endpoint"],
                    p256dh=sub["p256dh"],
                    auth_key=sub["auth_key"],
                ),
                title=title,
                body=body,
                icon="/favicon.ico",
                data=data,
            )
        except PushEndpointGoneError:
            stale_endpoints.append(sub["endpoint"])
        except Exception as exc:
            logger.warning("Push extraction notify failed for %s: %s", sub["endpoint"], exc)

    for endpoint in stale_endpoints:
        try:
            sb.table("push_subscriptions").delete().eq("endpoint", endpoint).execute()  # type: ignore[union-attr]
        except Exception:
            pass
