import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from core.auth import require_admin
from core.config import settings
from core.database import get_supabase
from services.geocoding import geocode_address

router = APIRouter()


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


class SupermarketCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    address: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None
    lat: float | None = None
    lng: float | None = None


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
        resp = (
            sb.table("supermarkets")
            .select("*, offers!inner(id)")
            .eq("is_active", True)
            .eq("offers.is_active", True)
            .eq("offers.is_confirmed", True)
            .order("name")
            .execute()
        )
        return [{k: v for k, v in row.items() if k != "offers"} for row in resp.data]
    resp = sb.table("supermarkets").select("*").eq("is_active", True).order("name").execute()
    return resp.data


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_supermarket(
    body: SupermarketCreate,
    _admin: Annotated[dict, Depends(require_admin)],
) -> dict:
    """Create a new supermarket branch. Admin only."""
    sb = get_supabase()
    lat, lng = body.lat, body.lng
    if lat is None and body.address and settings.geocoding_provider == "nominatim":
        full_addr = f"{body.address}, {body.postal_code} {body.city} {body.province}".strip()
        coords = geocode_address(full_addr)
        if coords:
            lat, lng = coords
    slug = _unique_slug(sb, _make_slug(body.name))
    row = {
        "name": body.name,
        "slug": slug,
        "address": body.address,
        "city": body.city,
        "province": body.province,
        "postal_code": body.postal_code,
        "lat": lat,
        "lng": lng,
        "is_active": True,
    }
    resp = sb.table("supermarkets").insert(row).execute()
    return resp.data[0]
