"""Web Push notification service using pywebpush."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from jose import jwt
from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]

from core.config import settings

logger = logging.getLogger(__name__)
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class PushSubscription:
    endpoint: str
    p256dh: str
    auth_key: str


class PushEndpointGoneError(Exception):
    """Raised when a push endpoint returns 410 Gone (subscription revoked by browser)."""


def _persist_notification(
    sb: object,
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    data: dict,
) -> None:
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
        logger.warning("Failed to persist app_notification for user %s: %s", user_id, exc)


def _load_push_subscriptions(sb: object, user_id: str) -> list[dict]:
    try:
        result = (
            sb.table("push_subscriptions")  # type: ignore[union-attr]
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.warning("Failed to fetch push subscriptions for user %s: %s", user_id, exc)
        return []


def _web_push_subscriptions(subscriptions: list[dict]) -> list[dict]:
    return [
        sub for sub in subscriptions if sub.get("channel", "web_push") == "web_push"
    ]


def _native_push_subscriptions(subscriptions: list[dict]) -> list[dict]:
    return [
        sub for sub in subscriptions if sub.get("channel") == "native_fcm" and sub.get("token")
    ]


def _delete_stale_push_endpoints(sb: object, endpoints: list[str]) -> None:
    for endpoint in endpoints:
        try:
            sb.table("push_subscriptions").delete().eq("endpoint", endpoint).execute()  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Failed to delete stale push endpoint %s: %s", endpoint, exc)


def _delete_stale_native_tokens(sb: object, tokens: list[str]) -> None:
    for token in tokens:
        try:
            sb.table("push_subscriptions").delete().eq("channel", "native_fcm").eq("token", token).execute()  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Failed to delete stale FCM token %s: %s", token, exc)


def _send_push_to_user(
    sb: object,
    *,
    user_id: str,
    title: str,
    body: str,
    data: dict,
) -> None:
    subscriptions = _load_push_subscriptions(sb, user_id)
    if not subscriptions:
        return

    stale_endpoints: list[str] = []
    for sub in _web_push_subscriptions(subscriptions):
        try:
            send_push_notification(
                subscription=PushSubscription(
                    endpoint=sub["endpoint"],
                    p256dh=sub["p256dh"],
                    auth_key=sub["auth_key"],
                ),
                title=title,
                body=body,
                data=data,
            )
        except PushEndpointGoneError:
            stale_endpoints.append(sub["endpoint"])
        except Exception as exc:
            logger.warning("Push notify failed for %s: %s", sub["endpoint"], exc)

    stale_tokens: list[str] = []
    for sub in _native_push_subscriptions(subscriptions):
        try:
            send_native_push_notification(
                token=str(sub["token"]),
                title=title,
                body=body,
                data=data,
            )
        except NativePushTokenGoneError:
            stale_tokens.append(str(sub["token"]))
        except Exception as exc:
            logger.warning("Native push notify failed for %s: %s", sub["token"], exc)

    _delete_stale_push_endpoints(sb, stale_endpoints)
    _delete_stale_native_tokens(sb, stale_tokens)


def send_push_to_user(
    sb: object,
    *,
    user_id: str,
    title: str,
    body: str,
    data: dict,
) -> None:
    _send_push_to_user(sb, user_id=user_id, title=title, body=body, data=data)


def _offer_is_currently_active(record: dict) -> bool:
    today = date.today().isoformat()
    valid_from = record.get("valid_from")
    valid_to = record.get("valid_to")
    if valid_from and valid_from > today:
        return False
    if valid_to and valid_to < today:
        return False
    return True


def _offers_url(*, supermarket_id: str | None) -> str:
    params = ["sort=published_at", "scroll=offers"]
    if supermarket_id:
        params.append(f"supermarket_id={supermarket_id}")
    return f"/offerte?{'&'.join(params)}"


def _flyer_published_url(*, supermarket_id: str | None) -> str:
    return _offers_url(supermarket_id=supermarket_id)


def _find_existing_notification(
    sb: object,
    *,
    user_id: str,
    kind: str,
    aggregation_key: str,
) -> dict | None:
    resp = (
        sb.table("app_notifications")  # type: ignore[union-attr]
        .select("id, data")
        .eq("user_id", user_id)
        .eq("kind", kind)
        .contains("data", {"aggregation_key": aggregation_key})
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = resp.data if isinstance(resp.data, list) else []
    return rows[0] if rows else None


def _persist_idempotent_notification(
    sb: object,
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    data: dict[str, object],
) -> bool:
    existing = _find_existing_notification(
        sb,
        user_id=user_id,
        kind=kind,
        aggregation_key=str(data["aggregation_key"]),
    )
    if not existing:
        _persist_notification(sb, user_id=user_id, kind=kind, title=title, body=body, data=data)
        return True
    sb.table("app_notifications").update(  # type: ignore[union-attr]
        {"title": title, "body": body, "data": data, "read_at": None}
    ).eq("id", existing["id"]).execute()
    return False


def _flyer_published_body(products_count: int) -> str:
    if products_count == 1:
        return "1 nuova offerta disponibile"
    return f"{products_count} nuove offerte disponibili"


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


class NativePushTokenGoneError(Exception):
    """Raised when FCM reports a token is invalid or unregistered."""


def _fcm_is_configured() -> bool:
    return bool(
        settings.fcm_enabled
        and settings.fcm_project_id
        and settings.fcm_client_email
        and settings.fcm_private_key
    )


def _fcm_private_key() -> str:
    return settings.fcm_private_key.replace("\\n", "\n")


def _fcm_access_token() -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": settings.fcm_client_email,
        "scope": _FCM_SCOPE,
        "aud": _FCM_TOKEN_URL,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=55)).timestamp()),
    }
    assertion = jwt.encode(claims, _fcm_private_key(), algorithm="RS256")
    resp = httpx.post(
        _FCM_TOKEN_URL,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


def _fcm_url() -> str:
    return f"https://fcm.googleapis.com/v1/projects/{settings.fcm_project_id}/messages:send"


def _fcm_data(data: dict | None) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key, value in (data or {}).items():
        if isinstance(value, str):
            payload[key] = value
        else:
            payload[key] = json.dumps(value)
    return payload


def _fcm_message(token: str, title: str, body: str, data: dict | None) -> dict:
    return {
        "token": token,
        "notification": {"title": title, "body": body},
        "data": _fcm_data(data),
        "android": {"notification": {"icon": "ic_notification", "color": "#1E7A45"}},
        "apns": {"payload": {"aps": {"sound": "default"}}},
    }


def send_native_push_notification(
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    if not _fcm_is_configured():
        return
    payload = {"message": _fcm_message(token, title, body, data)}
    resp = httpx.post(
        _fcm_url(),
        headers={"Authorization": f"Bearer {_fcm_access_token()}"},
        json=payload,
        timeout=10,
    )
    if resp.status_code in {400, 404} and "UNREGISTERED" in resp.text:
        raise NativePushTokenGoneError(token)
    resp.raise_for_status()


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

    _persist_notification(
        sb,
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        data=data,
    )
    _send_push_to_user(sb, user_id=user_id, title=title, body=body, data=data)


def deliver_public_flyer_published_to_recipient(
    sb: object,
    *,
    flyer_id: str,
    supermarket_id: str,
    supermarket_name: str,
    products_count: int,
    user_id: str,
) -> None:
    profile = _notification_profile(sb, user_id)
    if not profile:
        return
    title = f"Nuovo volantino da {supermarket_name}"
    body = _flyer_published_body(products_count)
    should_push = _persist_idempotent_notification(
        sb,
        user_id=user_id,
        kind="flyer_published",
        title=title,
        body=body,
        data=_flyer_notification_data(flyer_id, supermarket_id, products_count),
    )
    if should_push and profile.get("notifications_enabled", True):
        _send_push_to_user(
            sb,
            user_id=user_id,
            title=title,
            body=body,
            data=_flyer_notification_data(flyer_id, supermarket_id, products_count),
        )


def _notification_profile(sb: object, user_id: str) -> dict | None:
    response = (
        sb.table("user_profiles")  # type: ignore[union-attr]
        .select("id, notifications_enabled")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return response.data if response is not None else None


def _flyer_notification_data(
    flyer_id: str, supermarket_id: str, products_count: int
) -> dict:
    return {
        "kind": "flyer_published",
        "flyer_id": flyer_id,
        "supermarket_id": supermarket_id,
        "aggregation_key": f"flyer-published:{flyer_id}",
        "products_count": products_count,
        "url": _flyer_published_url(supermarket_id=supermarket_id),
    }
