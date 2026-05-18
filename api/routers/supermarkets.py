import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from core.auth import require_admin
from core.config import settings
from core.database import get_supabase
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
    return [
        {**rows_by_id[row["id"]], "distance_km": distances[row["id"]]}
        for row in nearby_rows
        if row["id"] in rows_by_id
    ]


def _make_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return slug.strip("-")


def _unique_slug(sb, base: str) -> str:
    slug, i = base, 2
    while sb.table("supermarkets").select("id").eq("slug", slug).execute().data:
        slug, i = f"{base}-{i}", i + 1
    return slug


@router.get("")
async def list_supermarkets(
    has_active_offers: bool = Query(False),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    max_distance_km: float = Query(10.0, gt=0, le=100),
) -> list[dict]:
    """Return active supermarkets. Public endpoint — no auth required.

    has_active_offers=true: only supermarkets with ≥1 active confirmed offer.
    """
    sb = get_supabase()
    if lat is not None and lng is not None:
        nearby = _nearby_supermarkets(sb, lat, lng, max_distance_km)
        ids = [row["id"] for row in nearby]
        if not ids:
            return []
        resp = sb.table("supermarkets").select("*").in_("id", ids).execute()
        return _merge_distances(resp.data or [], nearby)

    if has_active_offers:
        resp = apply_current_offer_window(
            (
                sb.table("supermarkets")
                .select("*, offers!inner(id)")
                .eq("is_active", True)
                .eq("offers.is_confirmed", True)
                .order("name")
            ),
            reference_table="offers",
        ).execute()
        return [{k: v for k, v in row.items() if k != "offers"} for row in resp.data]
    resp = sb.table("supermarkets").select("*").eq("is_active", True).order("name").execute()
    return resp.data


def _upload_logo(sb, sm_id: str, logo_content: bytes, content_type: str) -> str:
    """Upload logo to storage and return public URL. Raises HTTP 500 on failure."""
    ext = LOGO_EXT[content_type]
    storage_path = f"{sm_id}.{ext}"
    try:
        sb.storage.from_("logos").upload(
            path=storage_path,
            file=logo_content,
            file_options={"content-type": content_type, "upsert": "true"},
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
        .select("id")
        .eq("id", supermarket_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supermarket not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        current = (
            sb.table("supermarkets")
            .select("*")
            .eq("id", supermarket_id)
            .maybe_single()
            .execute()
        )
        return current.data

    if "address" in updates and updates.get("lat") is None and settings.geocoding_provider == "nominatim":
        address = updates.get("address", "")
        city = updates.get("city", "")
        province = updates.get("province", "")
        postal_code = updates.get("postal_code", "")
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
        .select("id, logo_url")
        .eq("id", supermarket_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supermarket not found")

    old_logo_url = result.data.get("logo_url")
    if old_logo_url:
        prefix = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/logos/"
        old_path = old_logo_url.removeprefix(prefix)
        if old_path != old_logo_url:
            try:
                sb.storage.from_("logos").remove([old_path])
            except Exception:
                pass

    logo_url = _upload_logo(sb, supermarket_id, logo_content, logo.content_type)
    updated = (
        sb.table("supermarkets")
        .update({"logo_url": logo_url})
        .eq("id", supermarket_id)
        .execute()
    )
    return updated.data[0]
