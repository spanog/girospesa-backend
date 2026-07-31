"""Signed, short-lived location state for unauthenticated discovery."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from core.config import settings
from core.session import create_session_token, read_session_token

GUEST_LOCATION_COOKIE = "girospesa_guest_location"
GUEST_LOCATION_TYPE = "guest_location"
GUEST_LOCATION_RADIUS_KM = 10.0
GUEST_LOCATION_TTL_SECONDS = 60 * 60 * 24 * 30


def create_guest_location_token(lat: float, lng: float) -> str:
    return create_session_token(
        {"typ": GUEST_LOCATION_TYPE, "lat": lat, "lng": lng, "radius": GUEST_LOCATION_RADIUS_KM},
        lifetime_seconds=GUEST_LOCATION_TTL_SECONDS,
    )


def read_guest_location(token: str | None) -> tuple[float, float, float] | None:
    if not token:
        return None
    claims = read_session_token(token)
    if not claims or claims.get("typ") != GUEST_LOCATION_TYPE:
        return None
    return _location_from_claims(claims)


def _location_from_claims(claims: dict[str, Any]) -> tuple[float, float, float] | None:
    lat, lng, radius = claims.get("lat"), claims.get("lng"), claims.get("radius")
    numeric = (lat, lng, radius)
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in numeric):
        return None
    if not -90 <= lat <= 90 or not -180 <= lng <= 180 or radius != GUEST_LOCATION_RADIUS_KM:
        return None
    return float(lat), float(lng), float(radius)


def cookie_secure(origin: str | None = None) -> bool:
    if origin:
        return origin.startswith("https://")
    return settings.environment.lower() not in {"development", "test"}


def cookie_samesite(origin: str | None = None) -> str:
    return "none" if cookie_secure(origin) else "lax"


def guest_location_required(clear_cookie: bool) -> HTTPException:
    headers = {"Cache-Control": "no-store"}
    if clear_cookie:
        same_site = "None" if cookie_secure() else "Lax"
        cookie = f"{GUEST_LOCATION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite={same_site}"
        headers["Set-Cookie"] = f"{cookie}; Secure" if cookie_secure() else cookie
    return HTTPException(428, detail={"code": "guest_location_required"}, headers=headers)
