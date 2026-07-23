"""Web Push notification service using pywebpush."""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from jose import jwt
from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]

from core.config import settings

logger = logging.getLogger(__name__)
_EARTH_RADIUS_KM = 6371.0088
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_TOKEN_URL = "https://oauth2.googleapis.com/token"


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


def _profile_prefers_supermarket(profile: dict, supermarket: dict) -> bool:
    preferred = profile.get("preferred_supermarkets") or []
    if not isinstance(preferred, list):
        return False
    keys = {str(supermarket.get("id") or ""), str(supermarket.get("slug") or "")}
    return any(str(value) in keys for value in preferred)


def _profile_is_within_radius(profile: dict, supermarket: dict) -> bool:
    user_lat, user_lng = _profile_reference_point(profile)
    if user_lat is None or user_lng is None:
        return False
    max_distance_km = float(profile.get("max_distance_km") or 10)
    distance = _haversine_km(
        user_lat,
        user_lng,
        float(supermarket["lat"]),
        float(supermarket["lng"]),
    )
    return distance <= max_distance_km


def _offer_is_currently_active(record: dict) -> bool:
    today = date.today().isoformat()
    valid_from = record.get("valid_from")
    valid_to = record.get("valid_to")
    if valid_from and valid_from > today:
        return False
    if valid_to and valid_to < today:
        return False
    return True


def _offers_url(*, supermarket_id: str | None, favorites_only: bool) -> str:
    params = ["sort=published_at", "scroll=offers"]
    if favorites_only:
        params.insert(0, "favorites=1")
    if supermarket_id:
        params.append(f"context_supermarket_id={supermarket_id}")
    return f"/offerte?{'&'.join(params)}"


def _favorite_offer_url(*, supermarket_id: str | None) -> str:
    return _offers_url(supermarket_id=supermarket_id, favorites_only=True)


def _flyer_published_url(*, supermarket_id: str | None) -> str:
    return _offers_url(supermarket_id=supermarket_id, favorites_only=False)


def _favorite_offer_aggregation_key(flyer_id: str) -> str:
    return f"favorite-flyer:{flyer_id}"


def _favorite_offer_data(
    offer: dict,
    *,
    match_count: int,
    matched_product_ids: list[str] | None = None,
    matched_product_names: list[str] | None = None,
    matched_offer_labels: list[str] | None = None,
) -> dict[str, object]:
    product_id = str(offer["product_id"])
    flyer_id = str(offer["flyer_id"])
    data: dict[str, object] = {
        "kind": "favorite_offer",
        "url": _favorite_offer_url(
            supermarket_id=str(offer.get("supermarket_id") or ""),
        ),
        "product_id": product_id,
        "aggregation_key": _favorite_offer_aggregation_key(flyer_id),
        "match_count": match_count,
        "matched_product_ids": matched_product_ids or [product_id],
        "matched_product_names": matched_product_names or [],
        "matched_offer_labels": matched_offer_labels or [],
    }
    if offer.get("id"):
        data["offer_id"] = str(offer["id"])
    if offer.get("flyer_id"):
        data["flyer_id"] = str(offer["flyer_id"])
    if offer.get("supermarket_id"):
        data["supermarket_id"] = str(offer["supermarket_id"])
    return data


def _favorite_product_details(sb: object, product_id: str) -> dict[str, str]:
    product_resp = (
        sb.table("products")  # type: ignore[union-attr]
        .select("name, brand")
        .eq("id", product_id)
        .maybe_single()
        .execute()
    )
    product = product_resp.data if product_resp is not None else None
    if not product:
        return {"name": "prodotto", "brand": ""}
    return {
        "name": str(product.get("name") or "prodotto"),
        "brand": str(product.get("brand") or ""),
    }


def _favorite_offer_title(match_count: int, supermarket_name: str) -> str:
    count_label = "1 offerta" if match_count == 1 else f"{match_count} offerte"
    return f"{count_label} da {supermarket_name or 'supermercato'}"


def _favorite_offer_supermarket_name(sb: object, offer: dict) -> str:
    supermarket_name = ""
    supermarket_id = offer.get("supermarket_id")
    if supermarket_id:
        supermarket_resp = (
            sb.table("supermarkets")  # type: ignore[union-attr]
            .select("name")
            .eq("id", supermarket_id)
            .maybe_single()
            .execute()
        )
        supermarket = supermarket_resp.data if supermarket_resp is not None else None
        supermarket_name = supermarket.get("name") if supermarket else ""
    return supermarket_name


def _favorite_offer_label(product: dict[str, str], offer: dict) -> str:
    price = offer.get("discounted_price") or offer.get("original_price")
    price_label = f"€{price:.2f}" if price else "prezzo disponibile"
    product_name = product.get("name") or "prodotto"
    brand = product.get("brand") or ""
    product_label = f"{brand} - {product_name}" if brand else product_name
    return f"{product_label} a {price_label}"


def _favorite_offer_body(product: dict[str, str], offer: dict) -> str:
    return _favorite_offer_label(product, offer)


def _favorite_product_name(sb: object, product_id: str) -> str:
    return _favorite_product_details(sb, product_id)["name"]


def _merge_unique_strings(existing: list[object], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in existing:
        if isinstance(value, str) and value not in seen:
            seen.add(value)
            merged.append(value)
    for value in incoming:
        if value not in seen:
            seen.add(value)
            merged.append(value)
    return merged


def _favorite_offer_aggregate_title(match_count: int, supermarket_name: str) -> str:
    return _favorite_offer_title(match_count, supermarket_name)


def _favorite_offer_aggregate_body(matched_offer_labels: list[str]) -> str:
    preview = matched_offer_labels[:2]
    suffix_count = max(len(matched_offer_labels) - len(preview), 0)
    names_chunk = "; ".join(preview)
    if suffix_count > 0:
        names_chunk = f"{names_chunk} e altri {suffix_count}"
    if names_chunk:
        return names_chunk
    return "Prodotti preferiti in offerta"


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


def _find_existing_favorite_notification(
    sb: object,
    *,
    user_id: str,
    aggregation_key: str,
) -> dict | None:
    return _find_existing_notification(
        sb,
        user_id=user_id,
        kind="favorite_offer",
        aggregation_key=aggregation_key,
    )


def _persist_favorite_offer_notification(
    sb: object,
    *,
    user_id: str,
    title: str,
    body: str,
    data: dict[str, object],
) -> None:
    existing = _find_existing_favorite_notification(
        sb,
        user_id=user_id,
        aggregation_key=str(data["aggregation_key"]),
    )
    if not existing:
        _persist_notification(
            sb,
            user_id=user_id,
            kind="favorite_offer",
            title=title,
            body=body,
            data=data,
        )
        return

    sb.table("app_notifications").update(  # type: ignore[union-attr]
        {
            "title": title,
            "body": body,
            "data": data,
            "read_at": None,
        }
    ).eq("id", existing["id"]).execute()


def notify_favorite_offer_published(sb: object, offer: dict) -> None:
    product_id = offer.get("product_id")
    flyer_id = offer.get("flyer_id")
    if not product_id or not flyer_id or not offer.get("is_confirmed"):
        return
    if not _offer_is_currently_active(offer):
        return

    flyer_resp = (
        sb.table("flyers")  # type: ignore[union-attr]
        .select("is_public, status")
        .eq("id", flyer_id)
        .maybe_single()
        .execute()
    )
    flyer = flyer_resp.data if flyer_resp is not None else None
    if not flyer or not flyer.get("is_public") or flyer.get("status") != "done":
        return

    supermarket_name = _favorite_offer_supermarket_name(sb, offer)
    product_details = _favorite_product_details(sb, str(product_id))
    single_title = _favorite_offer_title(1, supermarket_name)
    single_body = _favorite_offer_body(product_details, offer)
    product_name = product_details["name"]
    favorites_resp = (
        sb.table("favorites")  # type: ignore[union-attr]
        .select("user_id")
        .eq("product_id", product_id)
        .execute()
    )
    for favorite in favorites_resp.data or []:
        user_id = favorite["user_id"]
        aggregation_key = _favorite_offer_aggregation_key(str(flyer_id))
        existing = _find_existing_favorite_notification(
            sb,
            user_id=user_id,
            aggregation_key=aggregation_key,
        )
        existing_data = existing.get("data", {}) if existing else {}
        if (
            isinstance(existing_data, dict)
            and str(product_id) in existing_data.get("matched_product_ids", [])
        ):
            continue
        matched_product_ids = _merge_unique_strings(
            existing_data.get("matched_product_ids", [])
            if isinstance(existing_data, dict)
            else [],
            [str(product_id)],
        )
        matched_product_names = _merge_unique_strings(
            existing_data.get("matched_product_names", [])
            if isinstance(existing_data, dict)
            else [],
            [product_name],
        )
        matched_offer_labels = _merge_unique_strings(
            existing_data.get("matched_offer_labels", [])
            if isinstance(existing_data, dict)
            else [],
            [single_body],
        )
        match_count = len(matched_product_ids)
        if match_count == 1:
            title = single_title
            body = single_body
        else:
            title = _favorite_offer_aggregate_title(match_count, supermarket_name)
            body = _favorite_offer_aggregate_body(matched_offer_labels)
        data = _favorite_offer_data(
            offer,
            match_count=match_count,
            matched_product_ids=matched_product_ids,
            matched_product_names=matched_product_names,
            matched_offer_labels=matched_offer_labels,
        )
        _persist_favorite_offer_notification(
            sb,
            user_id=user_id,
            title=title,
            body=body,
            data=data,
        )
        _send_push_to_user(sb, user_id=user_id, title=title, body=body, data=data)


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
        .select("id, slug, lat, lng")
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
            "id, role, home_lat, home_lng, search_lat, search_lng, "
            "max_distance_km, preferred_supermarkets"
        )
        .execute()
    )
    profiles = profiles_resp.data or []
    if not profiles:
        return

    data = {
        "kind": "flyer_published",
        "flyer_id": flyer_id,
        "supermarket_id": supermarket_id,
        "aggregation_key": f"flyer-published:{flyer_id}",
        "products_count": products_count,
        "url": _flyer_published_url(supermarket_id=supermarket_id),
    }

    for profile in profiles:
        is_preferred = _profile_prefers_supermarket(profile, supermarket)
        is_visible = _profile_is_within_radius(profile, supermarket)
        is_nearby_customer = profile.get("role") == "customer" and is_visible
        if not (is_preferred and is_visible) and not is_nearby_customer:
            continue
        title = f"Nuovo volantino da {supermarket_name}"
        body = _flyer_published_body(products_count)
        should_push = _persist_idempotent_notification(
            sb,
            user_id=profile["id"],
            kind="flyer_published",
            title=title,
            body=body,
            data=data,
        )
        if not should_push:
            continue
        _send_push_to_user(
            sb,
            user_id=profile["id"],
            title=title,
            body=body,
            data=data,
        )
