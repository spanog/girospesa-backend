"""Manual offer creation — not tied to a flyer."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from core.auth import get_optional_user_id, managed_supermarket_ids, require_admin_or_manager
from core.database import get_supabase
from core.guest_location import GUEST_LOCATION_COOKIE, guest_location_required, read_guest_location
from api.routers._nearby_supermarkets import nearby_supermarket_distances, request_location
from services.extraction.normalizer import normalize_unit_price_measure
from services.offer_visibility import apply_current_offer_window
from services.product_format import ProductFormat
from api.routers._offer_utils import build_offer_row, insert_and_fetch_offer

router = APIRouter()


def _offer_group_key(offer: dict) -> str:
    """Keep independently published offers separate from cloned flyer offers."""
    source_offer_id = offer.get("source_offer_id")
    return f"source:{source_offer_id}" if source_offer_id else f"offer:{offer['id']}"


def _deduplicate_nearby_offers(
    offers: list[dict], distances_by_supermarket_id: dict[str, float]
) -> list[dict]:
    """Choose nearest target for each cloned source offer with deterministic ties."""
    representatives: dict[str, dict] = {}
    for offer in offers:
        enriched = {
            **offer,
            "distance_km": distances_by_supermarket_id.get(offer["supermarket_id"]),
        }
        group_key = _offer_group_key(enriched)
        current = representatives.get(group_key)
        if current is None or _offer_distance_sort_key(enriched) < _offer_distance_sort_key(current):
            representatives[group_key] = enriched
    return sorted(
        representatives.values(),
        key=lambda offer: ((offer.get("name") or "").casefold(), offer["id"]),
    )


def _offer_distance_sort_key(offer: dict) -> tuple[float, str, str]:
    return (
        float(offer.get("distance_km") or float("inf")),
        offer.get("supermarket_id") or "",
        offer["id"],
    )


def _offer_summary(offers: list[dict]) -> dict:
    counts_by_supermarket_id: dict[str, int] = {}
    counts_by_supermarket_slug: dict[str, int] = {}
    for offer in offers:
        supermarket_id = offer.get("supermarket_id")
        if supermarket_id:
            counts_by_supermarket_id[supermarket_id] = (
                counts_by_supermarket_id.get(supermarket_id, 0) + 1
            )
        supermarket_slug = offer.get("supermarket_slug")
        if supermarket_slug:
            counts_by_supermarket_slug[supermarket_slug] = (
                counts_by_supermarket_slug.get(supermarket_slug, 0) + 1
            )
    return {
        "total": len(offers),
        "supermarket_count": len(counts_by_supermarket_id),
        "counts_by_supermarket_id": counts_by_supermarket_id,
        "counts_by_supermarket_slug": counts_by_supermarket_slug,
    }


def _supermarket_address(supermarket: dict, fallback_name: str | None) -> str | None:
    address = (supermarket.get("address") or "").strip()
    city = (supermarket.get("city") or "").strip()
    if not address:
        return city or None
    if not city:
        return address
    normalized_address = address.casefold()
    normalized_city = city.casefold()
    normalized_name = (supermarket.get("name") or fallback_name or "").strip().casefold()
    if normalized_address == normalized_city or (
        normalized_name and normalized_address.startswith(normalized_name)
        and normalized_address.endswith(normalized_city)
    ):
        return city
    return address if normalized_city in normalized_address else f"{address}, {city}"


@router.get("")
async def list_public_offers(
    q: str | None = Query(None),
    category: str | None = Query(None),
    supermarket_id: str | None = Query(None),
    supermarket_ids: list[str] = Query(default=[]),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    request: Request = None,
    user_id: str | None = Depends(get_optional_user_id),
) -> dict:
    """Return currently visible offers; offer fields are self-contained."""
    sb = get_supabase()
    query = (
        sb.table("offers")
        .select("*, supermarkets(name, slug, logo_url, address, city)", count="exact")
        .eq("is_confirmed", True)
        .eq("offer_kind", "published_target")
    )
    query = apply_current_offer_window(query)
    guest_token = request.cookies.get(GUEST_LOCATION_COOKIE) if user_id is None else None
    guest_location = read_guest_location(guest_token)
    if user_id is None and guest_location is None:
        raise guest_location_required(clear_cookie=guest_token is not None)
    location = request_location(sb, user_id, guest_location)
    distances_by_supermarket_id: dict[str, float] | None = None
    if location is not None:
        user_lat, user_lng, radius = location
        distances_by_supermarket_id = nearby_supermarket_distances(
            sb, user_lat, user_lng, radius
        )
        if not distances_by_supermarket_id:
            return {"items": [], "total": 0, "nextPage": None}
        query = query.in_("supermarket_id", list(distances_by_supermarket_id))
    if q:
        query = query.ilike("name", f"%{q.strip()}%")
    if category:
        query = query.eq("category", category)
    if supermarket_id:
        query = query.eq("supermarket_id", supermarket_id)
    if supermarket_ids:
        query = query.in_("supermarket_id", list(dict.fromkeys(supermarket_ids)))

    ordered_query = query.order("name")
    if distances_by_supermarket_id is None:
        response = ordered_query.range(offset, offset + limit - 1).execute()
        items = _serialize_offers(response.data or [])
        total = response.count or 0
        return {
            "items": items,
            "total": total,
            "nextPage": offset + limit if offset + limit < total else None,
        }

    response = ordered_query.execute()
    offers = _serialize_offers(response.data or [])
    offers = _deduplicate_nearby_offers(offers, distances_by_supermarket_id)
    summary = _offer_summary(offers)
    total = summary["total"]
    items = offers[offset : offset + limit]
    return {
        "items": items,
        **summary,
        "nextPage": offset + limit if offset + limit < total else None,
    }


def _serialize_offers(rows: list[dict]) -> list[dict]:
    offers = []
    for row in rows:
        supermarket = row.pop("supermarkets", None) or {}
        offers.append({
            **row,
            "supermarket_name": supermarket.get("name") or row.get("supermarket_name"),
            "supermarket_slug": supermarket.get("slug"),
            "supermarket_logo_url": supermarket.get("logo_url"),
            "supermarket_address": _supermarket_address(
                supermarket, row.get("supermarket_name")
            ),
        })
    return offers


class ManualOfferCreate(BaseModel):
    supermarket_id: str
    name: str = Field(..., min_length=1)
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    format: ProductFormat = Field(default_factory=ProductFormat)
    price_offer: float = Field(..., gt=0)
    price_original: float | None = Field(None, gt=0)
    unit_price_value: float | None = Field(None, gt=0)
    unit_price_unit: str | None = None
    offer_notes: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_manual_offer(
    payload: ManualOfferCreate,
    profile: Annotated[dict, Depends(require_admin_or_manager)],
) -> dict:
    if profile.get("role") == "supermarket_manager":
        if payload.supermarket_id not in managed_supermarket_ids(profile):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Managers can only create offers for their own supermarket")
    sb = get_supabase()
    sm = sb.table("supermarkets").select("id, name").eq("id", payload.supermarket_id).maybe_single().execute()
    if not sm or not sm.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supermarket not found")
    normalized_unit = normalize_unit_price_measure(payload.unit_price_unit) if payload.unit_price_unit else None
    offer_row = build_offer_row(payload, sm.data["id"], sm.data["name"], None, normalized_unit)
    return insert_and_fetch_offer(sb, offer_row)
