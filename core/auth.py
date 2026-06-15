"""
JWT authentication for FastAPI routes.
Prefers Supabase-issued Bearer JWTs, then falls back to legacy backend session cookies.
"""

from __future__ import annotations

import json
import time
from urllib.request import urlopen
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from core.config import settings
from core.session import read_session_token

_COOKIE_NAME = "girospesa_session"

_bearer = HTTPBearer()
_optional_bearer = HTTPBearer(auto_error=False)
_JWKS_CACHE_TTL_SECONDS = 300
_jwks_cache: dict[str, object] = {"value": None, "expires_at": 0.0}


def _jwks_url() -> str:
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _load_jwks() -> dict:
    now = time.time()
    if now < float(_jwks_cache["expires_at"]) and _jwks_cache["value"]:
        return _jwks_cache["value"]  # type: ignore[return-value]
    with urlopen(_jwks_url(), timeout=5) as response:
        jwks = json.load(response)
    _jwks_cache["value"] = jwks
    _jwks_cache["expires_at"] = now + _JWKS_CACHE_TTL_SECONDS
    return jwks


def _decode_token(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        key: str | dict
        if algorithm == "HS256":
            key = settings.supabase_jwt_secret
        elif algorithm == "ES256":
            key = _load_jwks()
        else:
            raise JWTError("Unsupported JWT algorithm")
        payload = jwt.decode(token, key, algorithms=[algorithm], options={"verify_aud": False})
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)],
    session_cookie: Annotated[str | None, Cookie(alias=_COOKIE_NAME)] = None,
) -> dict:
    """Dependency: resolves to the decoded JWT payload (user_id at payload['sub']).

    Checks Supabase Bearer JWT first; falls back to legacy backend session cookie.
    """
    if credentials is not None:
        return _decode_token(credentials.credentials)

    if session_cookie:
        payload = read_session_token(session_cookie)
        if payload:
            return payload

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


async def get_current_user_id(
    user: Annotated[dict, Depends(get_current_user)],
) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing sub claim")
    return user_id


async def get_optional_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)],
    session_cookie: Annotated[str | None, Cookie(alias=_COOKIE_NAME)] = None,
) -> str | None:
    """Dependency: returns the user_id (sub) from Bearer JWT or session cookie, else None."""
    if credentials is not None:
        try:
            payload = _decode_token(credentials.credentials)
            return payload.get("sub")
        except HTTPException:
            return None

    if session_cookie:
        payload = read_session_token(session_cookie)
        if payload and payload.get("sub"):
            return payload["sub"]
    return None


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)],
    session_cookie: Annotated[str | None, Cookie(alias=_COOKIE_NAME)] = None,
) -> dict | None:
    """Dependency: returns decoded Bearer/session payload when available, else None."""
    if credentials is not None:
        try:
            return _decode_token(credentials.credentials)
        except HTTPException:
            return None

    if session_cookie:
        payload = read_session_token(session_cookie)
        if payload:
            return payload
    return None


async def require_admin(
    profile: Annotated[dict, Depends(get_current_user_profile)],
) -> dict:
    """Dependency: requires admin role using server-side profile data."""
    if profile.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return profile


async def get_current_user_profile(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Fetch user profile plus managed supermarket assignments."""
    from core.database import get_supabase

    sb = get_supabase()
    result = (
        sb.table("user_profiles")
        .select("*")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User profile not found",
        )
    profile = result.data
    manager_ids_result = (
        sb.table("manager_supermarkets")
        .select("supermarket_id")
        .eq("user_id", user_id)
        .execute()
    )
    manager_ids = [
        row["supermarket_id"]
        for row in (manager_ids_result.data or [])
        if row.get("supermarket_id")
    ]
    if not manager_ids and profile.get("managed_supermarket_id"):
        manager_ids = [profile["managed_supermarket_id"]]
    profile["managed_supermarket_ids"] = manager_ids
    return profile


_PRIVILEGED_ROLES = frozenset({"admin", "supermarket_manager"})


async def require_admin_or_manager(
    profile: Annotated[dict, Depends(get_current_user_profile)],
) -> dict:
    """Requires role in ('admin', 'supermarket_manager'). Returns full profile."""
    if profile.get("role") not in _PRIVILEGED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or supermarket manager access required",
        )
    return profile


def managed_supermarket_ids(profile: dict) -> list[str]:
    ids = profile.get("managed_supermarket_ids")
    if isinstance(ids, list):
        return [value for value in ids if isinstance(value, str) and value]
    managed = profile.get("managed_supermarket_id")
    return [managed] if isinstance(managed, str) and managed else []


def assert_flyer_access(profile: dict, flyer: dict) -> None:
    """If manager: flyer.supermarket_id must be in assigned supermarkets."""
    if profile.get("role") != "supermarket_manager":
        return
    if flyer.get("supermarket_id") not in managed_supermarket_ids(profile):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: flyer belongs to a different supermarket",
        )
