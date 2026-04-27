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
