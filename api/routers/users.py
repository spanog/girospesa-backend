from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from core.auth import get_current_user_id
from core.database import get_supabase
from services.geocoding import geocode_address

router = APIRouter()

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
    notification_expiry: bool | None = None
    notification_deals: bool | None = None
    notification_favorites: bool | None = None
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

    resp = (
        sb.table("user_profiles")
        .update(update_data)
        .eq("id", user_id)
        .execute()
    )
    return resp.data[0]


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
    password: str = Field(min_length=8)


@router.post("/me/password", status_code=204)
async def update_password(
    body: UpdatePasswordBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    sb.auth.admin.update_user_by_id(user_id, {"password": body.password})
    return Response(status_code=204)


@router.delete("/me", status_code=204)
async def delete_account(user_id: Annotated[str, Depends(get_current_user_id)]) -> Response:
    """Permanently delete the authenticated user's account and all their data."""
    sb = get_supabase()
    # Cascade deletes on all FK-linked tables are defined in the DB schema.
    sb.auth.admin.delete_user(user_id)
    return Response(status_code=204)
