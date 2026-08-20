import hashlib
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

from core.auth import get_optional_user_id, require_admin
from core.config import settings
from core.database import get_supabase
from core.guest_location import GUEST_LOCATION_COOKIE, guest_location_required, read_guest_location
from api.routers._nearby_supermarkets import request_location
from services.geocoding import geocode_address
from services.offer_visibility import apply_current_offer_window

router = APIRouter()


class SupermarketUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None
    lat: float | None = None
    lng: float | None = None

ALLOWED_LOGO_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2 MB
LOGO_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
LOGO_CACHE_CONTROL = "31536000"


def _nearby_supermarkets(sb, lat: float, lng: float, max_distance_km: float) -> list[dict]:
    response = sb.rpc(
        "nearby_supermarkets",
        {
            "user_lat": lat,
            "user_lng": lng,
            "radius_m": max_distance_km * 1000,
        },
    ).execute()
    return response.data or []


def _merge_distances(rows: list[dict], nearby_rows: list[dict]) -> list[dict]:
    rows_by_id = {row["id"]: row for row in rows}
    distances = {row["id"]: row["distance_km"] for row in nearby_rows}
    merged = [
        {**row, "distance_km": distances[row["id"]]}
        for row in rows
        if row["id"] in distances
    ]
    return sorted(merged, key=lambda row: (row["distance_km"], row["name"]))


def _make_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return slug.strip("-")


def _unique_slug(sb, base: str) -> str:
    slug, i = base, 2
    while sb.table("supermarkets").select("id").eq("slug", slug).execute().data:
        slug, i = f"{base}-{i}", i + 1
    return slug


def _supermarkets_with_active_offers(sb, ids: list[str] | None = None) -> list[dict]:
    query = (
        sb.table("supermarkets")
        .select("*, offers!inner(id)")
        .eq("is_active", True)
        .eq("offers.is_confirmed", True)
    )
    if ids:
        query = query.in_("id", ids)
    rows = apply_current_offer_window(query, reference_table="offers").execute().data or []
    return [{key: value for key, value in row.items() if key != "offers"} for row in rows]


@router.get("")
async def list_supermarkets(
    with_active_offers: bool = Query(False),
    request: Request = None,
    user_id: str | None = Depends(get_optional_user_id),
) -> list[dict]:
    """Return active supermarkets inside caller's active radius."""
    sb = get_supabase()
    guest_token = request.cookies.get(GUEST_LOCATION_COOKIE) if user_id is None else None
    guest_location = read_guest_location(guest_token)
    if user_id is None and guest_location is None:
        raise guest_location_required(clear_cookie=guest_token is not None)
    location = request_location(sb, user_id, guest_location)
    if location is not None:
        user_lat, user_lng, radius = location
        nearby = _nearby_supermarkets(sb, user_lat, user_lng, radius)
        ids = [row["id"] for row in nearby]
        if not ids:
            return []
        if with_active_offers:
            active_rows = _supermarkets_with_active_offers(sb, ids)
            return _merge_distances(active_rows, nearby)
        nearby_rows = []
        if ids:
            resp = sb.table("supermarkets").select("*").in_("id", ids).execute()
            nearby_rows = _merge_distances(resp.data or [], nearby)
        return nearby_rows
    return []


def _logo_storage_path(sm_id: str, logo_content: bytes, content_type: str) -> str:
    digest = hashlib.sha256(logo_content).hexdigest()
    return f"{sm_id}/{digest}.{LOGO_EXT[content_type]}"


def _upload_logo(sb, sm_id: str, logo_content: bytes, content_type: str) -> str:
    """Upload an immutable logo asset and return its public URL."""
    storage_path = _logo_storage_path(sm_id, logo_content, content_type)
    try:
        sb.storage.from_("logos").upload(
            path=storage_path,
            file=logo_content,
            file_options={
                "content-type": content_type,
                "cache-control": LOGO_CACHE_CONTROL,
                "upsert": "true",
            },
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Logo upload failed",
        )
    return sb.storage.from_("logos").get_public_url(storage_path)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_supermarket(
    name: Annotated[str, Form()],
    logo: Annotated[UploadFile, File()],
    address: Annotated[str | None, Form()] = None,
    city: Annotated[str | None, Form()] = None,
    province: Annotated[str | None, Form()] = None,
    postal_code: Annotated[str | None, Form()] = None,
    lat: Annotated[float | None, Form()] = None,
    lng: Annotated[float | None, Form()] = None,
    _admin: Annotated[dict, Depends(require_admin)] = None,
) -> dict:
    """Create a new supermarket branch with required logo. Admin only."""
    if not logo.content_type or logo.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported logo type: {logo.content_type}",
        )
    logo_content = await logo.read()
    if len(logo_content) > MAX_LOGO_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Logo exceeds {MAX_LOGO_SIZE // (1024 * 1024)} MB limit",
        )

    if lat is None and address and settings.geocoding_provider == "nominatim":
        full_addr = ", ".join(p for p in [address, postal_code, city, province] if p)
        coords = geocode_address(full_addr)
        if coords:
            lat, lng = coords

    sb = get_supabase()
    slug = _unique_slug(sb, _make_slug(name))
    row = {
        "name": name,
        "slug": slug,
        "address": address,
        "city": city,
        "province": province,
        "postal_code": postal_code,
        "lat": lat,
        "lng": lng,
        "is_active": True,
    }
    resp = sb.table("supermarkets").insert(row).execute()
    sm = resp.data[0]
    sm_id = sm["id"]

    try:
        logo_url = _upload_logo(sb, sm_id, logo_content, logo.content_type)
    except HTTPException:
        sb.table("supermarkets").delete().eq("id", sm_id).execute()
        raise

    updated = (
        sb.table("supermarkets")
        .update({"logo_url": logo_url})
        .eq("id", sm_id)
        .execute()
    )
    return updated.data[0]


@router.patch("/{supermarket_id}")
async def update_supermarket(
    supermarket_id: str,
    body: SupermarketUpdate,
    _admin: Annotated[dict, Depends(require_admin)] = None,
) -> dict:
    """Update supermarket info fields. Admin only."""
    sb = get_supabase()
    result = (
        sb.table("supermarkets")
        .select("*")
        .eq("id", supermarket_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supermarket not found")

    existing_row = result.data
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return existing_row

    if "address" in updates and "lat" not in updates and settings.geocoding_provider == "nominatim":
        address = updates.get("address") or existing_row.get("address", "")
        city = updates.get("city") or existing_row.get("city", "")
        province = updates.get("province") or existing_row.get("province", "")
        postal_code = updates.get("postal_code") or existing_row.get("postal_code", "")
        full_addr = ", ".join(p for p in [address, postal_code, city, province] if p)
        coords = geocode_address(full_addr)
        if coords:
            updates["lat"], updates["lng"] = coords

    updated = (
        sb.table("supermarkets")
        .update(updates)
        .eq("id", supermarket_id)
        .execute()
    )
    return updated.data[0]


@router.patch("/{supermarket_id}/logo")
async def update_supermarket_logo(
    supermarket_id: str,
    logo: Annotated[UploadFile, File()],
    _admin: Annotated[dict, Depends(require_admin)] = None,
) -> dict:
    """Update the logo for an existing supermarket. Admin only."""
    if not logo.content_type or logo.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported logo type: {logo.content_type}",
        )
    logo_content = await logo.read()
    if len(logo_content) > MAX_LOGO_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Logo exceeds {MAX_LOGO_SIZE // (1024 * 1024)} MB limit",
        )

    sb = get_supabase()
    result = (
        sb.table("supermarkets")
        .select("id")
        .eq("id", supermarket_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supermarket not found")

    logo_url = _upload_logo(sb, supermarket_id, logo_content, logo.content_type)
    updated = (
        sb.table("supermarkets")
        .update({"logo_url": logo_url})
        .eq("id", supermarket_id)
        .execute()
    )
    return updated.data[0]
