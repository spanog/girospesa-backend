"""Web Push notification service using pywebpush."""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass

from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]

from core.config import settings

logger = logging.getLogger(__name__)
_EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class PushSubscription:
    endpoint: str
    p256dh: str
    auth_key: str


class PushEndpointGoneError(Exception):
    """Raised when a push endpoint returns 410 Gone (subscription revoked by browser)."""


def _haversine_km(
    origin_lat: float,
    origin_lng: float,
    target_lat: float,
    target_lng: float,
) -> float:
    origin_lat_rad = math.radians(origin_lat)
    target_lat_rad = math.radians(target_lat)
    delta_lat = math.radians(target_lat - origin_lat)
    delta_lng = math.radians(target_lng - origin_lng)

    sin_lat = math.sin(delta_lat / 2)
    sin_lng = math.sin(delta_lng / 2)
    arc = (
        sin_lat * sin_lat
        + math.cos(origin_lat_rad) * math.cos(target_lat_rad) * sin_lng * sin_lng
    )
    return _EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(arc), math.sqrt(1 - arc))


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


def _delete_stale_push_endpoints(sb: object, endpoints: list[str]) -> None:
    for endpoint in endpoints:
        try:
            sb.table("push_subscriptions").delete().eq("endpoint", endpoint).execute()  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Failed to delete stale push endpoint %s: %s", endpoint, exc)


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
                data=data,
            )
        except PushEndpointGoneError:
            stale_endpoints.append(sub["endpoint"])
        except Exception as exc:
            logger.warning("Push notify failed for %s: %s", sub["endpoint"], exc)

    _delete_stale_push_endpoints(sb, stale_endpoints)


def _profile_pref_enabled(profile: dict | None, key: str) -> bool:
    if profile is None:
        return True
    return profile.get(key, True)


def _profile_reference_point(profile: dict) -> tuple[float | None, float | None]:
    lat = profile.get("search_lat")
    lng = profile.get("search_lng")
    if lat is not None and lng is not None:
        return float(lat), float(lng)
    home_lat = profile.get("home_lat")
    home_lng = profile.get("home_lng")
    if home_lat is None or home_lng is None:
        return None, None
    return float(home_lat), float(home_lng)


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

    _persist_notification(
        sb,
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        data=data,
    )
    _send_push_to_user(sb, user_id=user_id, title=title, body=body, data=data)


def notify_public_flyer_published(
    sb: object,
    *,
    flyer_id: str,
    supermarket_id: str,
    supermarket_name: str,
    products_count: int,
) -> None:
    supermarket_resp = (
        sb.table("supermarkets")  # type: ignore[union-attr]
        .select("id, lat, lng")
        .eq("id", supermarket_id)
        .maybe_single()
        .execute()
    )
    supermarket = supermarket_resp.data if supermarket_resp is not None else None
    if not supermarket or supermarket.get("lat") is None or supermarket.get("lng") is None:
        logger.warning(
            "Skipping flyer publication notifications for %s: missing supermarket coordinates",
            flyer_id,
        )
        return

    profiles_resp = (
        sb.table("user_profiles")  # type: ignore[union-attr]
        .select(
            "id, role, notification_deals, home_lat, home_lng, search_lat, search_lng, max_distance_km"
        )
        .eq("role", "customer")
        .eq("notification_deals", True)
        .execute()
    )
    profiles = profiles_resp.data or []
    if not profiles:
        return

    title = "Nuovo volantino vicino a te"
    body = f"{supermarket_name}: {products_count} offerte nuove disponibili vicino a te"
    data = {
        "kind": "flyer_published",
        "flyer_id": flyer_id,
        "supermarket_id": supermarket_id,
        "products_count": products_count,
        "url": "/volantini",
    }

    target_lat = float(supermarket["lat"])
    target_lng = float(supermarket["lng"])

    for profile in profiles:
        if not _profile_pref_enabled(profile, "notification_deals"):
            continue
        user_lat, user_lng = _profile_reference_point(profile)
        if user_lat is None or user_lng is None:
            continue
        max_distance_km = float(profile.get("max_distance_km") or 10)
        if _haversine_km(user_lat, user_lng, target_lat, target_lng) > max_distance_km:
            continue
        _persist_notification(
            sb,
            user_id=profile["id"],
            kind="flyer_published",
            title=title,
            body=body,
            data=data,
        )
        _send_push_to_user(
            sb,
            user_id=profile["id"],
            title=title,
            body=body,
            data=data,
        )
