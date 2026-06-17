from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from core.auth import get_current_user_id
from core.config import settings
from core.database import get_supabase
from core.supabase_client import create_supabase_client as create_client
from services.geocoding import geocode_address

router = APIRouter()
logger = logging.getLogger(__name__)

_AVATAR_BUCKET = "avatars"
_AVATAR_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_AVATAR_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


class UpdateProfileBody(BaseModel):
    display_name: str | None = None
    home_address: str | None = None
    home_city: str | None = None
    home_province: str | None = None
    home_postal_code: str | None = None
    max_distance_km: int | None = Field(default=None, ge=1, le=100)
    notifications_enabled: bool | None = None
    preferred_supermarkets: list[str] | None = None
    search_label: str | None = None
    search_lat: float | None = None
    search_lng: float | None = None


class GeocodeBody(BaseModel):
    address: str


def _point_wkt(lat: float, lng: float) -> str:
    return f"SRID=4326;POINT({lng} {lat})"


@router.get("/me")
async def get_profile(user_id: Annotated[str, Depends(get_current_user_id)]) -> dict:
    sb = get_supabase()
    resp = sb.table("user_profiles").select("*").eq("id", user_id).single().execute()
    return resp.data


@router.put("/me")
async def update_profile(
    body: UpdateProfileBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    update_data = body.model_dump(exclude_none=True)

    # If address fields changed, re-geocode
    if any(k in update_data for k in ("home_address", "home_city", "home_province", "home_postal_code")):
        profile = sb.table("user_profiles").select("*").eq("id", user_id).single().execute().data
        addr = (
            f"{update_data.get('home_address', profile.get('home_address', ''))},"
            f"{update_data.get('home_postal_code', profile.get('home_postal_code', ''))}"
            f"{update_data.get('home_city', profile.get('home_city', ''))}"
            f"{update_data.get('home_province', profile.get('home_province', ''))}"
        )
        coords = geocode_address(addr)
        if coords:
            update_data["home_lat"], update_data["home_lng"] = coords
            update_data["home_location"] = _point_wkt(coords[0], coords[1])

    if "search_lat" in update_data and "search_lng" in update_data:
        update_data["search_location"] = _point_wkt(
            update_data["search_lat"],
            update_data["search_lng"],
        )

    sb.table("user_profiles").update(update_data).eq("id", user_id).execute()
    if update_data.get("notifications_enabled") is False:
        sb.table("push_subscriptions").delete().eq("user_id", user_id).execute()
    profile = sb.table("user_profiles").select("*").eq("id", user_id).single().execute()
    if not profile.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile.data


@router.post("/geocode")
async def geocode_user_address(
    body: GeocodeBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Trigger geocoding after registration (called by frontend in background)."""
    coords = geocode_address(body.address)
    if coords:
        sb = get_supabase()
        sb.table("user_profiles").update({
            "home_lat": coords[0],
            "home_lng": coords[1],
            "home_location": _point_wkt(coords[0], coords[1]),
        }).eq("id", user_id).execute()
        return {"lat": coords[0], "lng": coords[1]}
    return {"lat": None, "lng": None}


@router.get("/me/favorites")
async def get_favorites(user_id: Annotated[str, Depends(get_current_user_id)]) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("favorites")
        .select("products(*)")
        .eq("user_id", user_id)
        .execute()
    )
    return [row["products"] for row in resp.data if row.get("products")]


@router.post("/me/favorites/{product_id}", status_code=201)
async def add_favorite(
    product_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    resp = sb.table("favorites").upsert({"user_id": user_id, "product_id": product_id}).execute()
    return resp.data[0]


@router.delete("/me/favorites/{product_id}", status_code=204)
async def remove_favorite(
    product_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    sb.table("favorites").delete().eq("user_id", user_id).eq("product_id", product_id).execute()
    return Response(status_code=204)


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Upload a new avatar image; returns the public URL stored in user_profiles."""
    if file.content_type not in _AVATAR_ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="Unsupported image type. Use JPEG, PNG or WebP.")

    data = await file.read()
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image must be smaller than 5 MB.")

    ext = file.content_type.split("/")[-1].replace("jpeg", "jpg")
    path = f"{user_id}.{ext}"

    sb = get_supabase()
    sb.storage.from_(_AVATAR_BUCKET).upload(
        path,
        data,
        file_options={"content-type": file.content_type, "upsert": "true"},
    )

    raw_url = sb.storage.from_(_AVATAR_BUCKET).get_public_url(path)
    avatar_url = f"{raw_url.rstrip('?')}?t={int(time.time())}"
    sb.table("user_profiles").update({"avatar_url": avatar_url}).eq("id", user_id).execute()
    return {"avatar_url": avatar_url}


class UpdatePasswordBody(BaseModel):
    current_password: str = Field(min_length=1)
    password: str = Field(min_length=8)


@router.post("/me/password", status_code=204)
async def update_password(
    body: UpdatePasswordBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    try:
        user_resp = sb.auth.admin.get_user_by_id(user_id)
        email = user_resp.user.email
        verify_client = create_client(settings.supabase_url, settings.supabase_secret_key)
        verify_client.auth.sign_in_with_password({"email": email, "password": body.current_password})
    except Exception:
        raise HTTPException(status_code=400, detail="Password attuale non corretta.")
    try:
        sb.auth.admin.update_user_by_id(user_id, {"password": body.password})
    except Exception:
        raise HTTPException(status_code=400, detail="Aggiornamento password fallito.")
    return Response(status_code=204)


def _is_missing_user_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = ("user not found", "not found", "does not exist")
    return any(marker in message for marker in markers)


def _cleanup_account_delete_dependencies(sb: object, user_id: str) -> None:
    # Historical invite FKs may block auth.users deletion unless they are
    # nulled or removed before the auth record is deleted.
    sb.table("list_members").update({"invited_by": None}).eq("invited_by", user_id).execute()
    sb.table("list_invites").update({"accepted_by": None}).eq("accepted_by", user_id).execute()
    sb.table("list_invites").delete().eq("invited_by", user_id).execute()


def _delete_auth_user(user_id: str) -> None:
    sb = get_supabase()
    try:
        _cleanup_account_delete_dependencies(sb, user_id)
        sb.auth.admin.delete_user(user_id)
    except Exception as exc:
        if _is_missing_user_error(exc):
            logger.info("Account already deleted", extra={"user_id": user_id})
            return
        logger.exception("Account deletion failed", extra={"user_id": user_id})
        raise HTTPException(
            status_code=502,
            detail="Eliminazione account non riuscita. Riprova tra qualche istante.",
        ) from exc


@router.delete("/me", status_code=204)
async def delete_account(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    """Permanently delete the authenticated user's account and all their data."""
    _delete_auth_user(user_id)
    return Response(status_code=204)
