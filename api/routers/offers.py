"""Manual offer creation — not tied to a flyer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from core.auth import get_optional_user_id, managed_supermarket_ids, require_admin_or_manager
from core.database import get_supabase
from core.guest_location import GUEST_LOCATION_COOKIE, guest_location_required, read_guest_location
from api.routers._nearby_supermarkets import (
    active_nearby_supermarkets,
    nearby_supermarket_distances,
    request_location,
)
from services.extraction.normalizer import normalize_unit_price_measure
from services.offer_visibility import apply_current_offer_window
from services.product_format import ProductFormat
from api.routers._offer_utils import build_offer_row, insert_and_fetch_offer

router = APIRouter()

PUBLIC_OFFER_SELECT = "*, supermarkets(name, slug, logo_url, municipality_code, municipalities(name,province_code))"
_NEARBY_OFFER_PAGE_RPC = "nearby_public_offer_page"


@dataclass(frozen=True)
class NearbyOfferPage:
    offer_ids: list[str]
    distances_by_offer_id: dict[str, float | None]
    total: int
    supermarket_count: int
    counts_by_supermarket_id: dict[str, int]
    counts_by_supermarket_slug: dict[str, int]

    def response(self, items: list[dict], offset: int, limit: int) -> dict:
        return {
            "items": items,
            "total": self.total,
            "supermarket_count": self.supermarket_count,
            "counts_by_supermarket_id": self.counts_by_supermarket_id,
            "counts_by_supermarket_slug": self.counts_by_supermarket_slug,
            "nextPage": offset + limit if offset + limit < self.total else None,
        }


def _supermarket_municipality(supermarket: dict) -> str | None:
    municipality = supermarket.get("municipalities") or {}
    name = municipality.get("name")
    province = municipality.get("province_code")
    if not name:
        return None
    return f"{name} ({province})" if province else name


def _public_offers_query(sb, *, exact_count: bool):
    offers = sb.table("offers")
    query = (
        offers.select(PUBLIC_OFFER_SELECT, count="exact")
        if exact_count
        else offers.select(PUBLIC_OFFER_SELECT)
    )
    query = query.eq("is_confirmed", True).eq("offer_kind", "published_target")
    return apply_current_offer_window(query)


def _filter_public_offers(
    query,
    *,
    q: str | None,
    category: str | None,
    subcategory: str | None,
    supermarket_id: str | None,
    supermarket_ids: list[str],
):
    if q:
        query = query.ilike("name", f"%{q.strip()}%")
    if category:
        query = query.eq("category", category)
    if subcategory:
        query = query.eq("subcategory", subcategory)
    if supermarket_id:
        query = query.eq("supermarket_id", supermarket_id)
    if supermarket_ids:
        query = query.in_("supermarket_id", list(dict.fromkeys(supermarket_ids)))
    return query


def _public_offers_response(
    sb,
    *,
    q: str | None,
    category: str | None,
    subcategory: str | None,
    supermarket_id: str | None,
    supermarket_ids: list[str],
    limit: int,
    offset: int,
    distances_by_supermarket_id: dict[str, float | None] | None,
) -> dict:
    if distances_by_supermarket_id is not None:
        return _nearby_public_offers_response(
            sb,
            q=q,
            category=category,
            subcategory=subcategory,
            supermarket_id=supermarket_id,
            supermarket_ids=supermarket_ids,
            limit=limit,
            offset=offset,
            distances_by_supermarket_id=distances_by_supermarket_id,
        )
    return _all_public_offers_response(
        sb, q, category, subcategory, supermarket_id, supermarket_ids, limit, offset
    )


def _all_public_offers_response(
    sb, q, category, subcategory, supermarket_id, supermarket_ids, limit, offset
) -> dict:
    query = _filter_public_offers(
        _public_offers_query(sb, exact_count=True),
        q=q,
        category=category,
        subcategory=subcategory,
        supermarket_id=supermarket_id,
        supermarket_ids=supermarket_ids,
    )
    ordered_query = query.order("name")
    response = ordered_query.range(offset, offset + limit - 1).execute()
    items = _serialize_offers(response.data or [])
    total = response.count or 0
    return _offer_page(items, total, offset, limit)


def _offer_page(items: list[dict], total: int, offset: int, limit: int) -> dict:
    return {
        "items": items,
        "total": total,
        "nextPage": offset + limit if offset + limit < total else None,
    }


def _nearby_public_offers_response(
    sb, *, q, category, subcategory, supermarket_id, supermarket_ids, limit, offset,
    distances_by_supermarket_id: dict[str, float | None],
) -> dict:
    page = _nearby_offer_page_from_database(
        sb, q, category, subcategory, supermarket_id, supermarket_ids, limit, offset,
        distances_by_supermarket_id,
    )
    items = _nearby_offer_items(sb, page.offer_ids, page.distances_by_offer_id)
    return page.response(items, offset, limit)


def _nearby_offer_page_from_database(
    sb, q, category, subcategory, supermarket_id, supermarket_ids, limit, offset, distances
) -> NearbyOfferPage:
    response = sb.rpc(
        _NEARBY_OFFER_PAGE_RPC,
        _nearby_offer_page_parameters(
            q, category, subcategory, supermarket_id, supermarket_ids, limit, offset, distances
        ),
    ).execute()
    return _nearby_offer_page_from_rows(response.data or [])


def _nearby_offer_page_parameters(
    q, category, subcategory, supermarket_id, supermarket_ids, limit, offset, distances
) -> dict:
    return {
        "candidate_supermarket_ids": list(distances),
        "candidate_distances_km": list(distances.values()),
        "filter_query": q,
        "filter_category": category,
        "filter_subcategory": subcategory,
        "filter_supermarket_id": supermarket_id,
        "filter_supermarket_ids": list(dict.fromkeys(supermarket_ids)),
        "page_limit": limit,
        "page_offset": offset,
    }


def _nearby_offer_page_from_rows(rows: list[dict]) -> NearbyOfferPage:
    first = rows[0] if rows else {}
    offer_ids = [str(row["id"]) for row in rows if row.get("id")]
    return NearbyOfferPage(
        offer_ids=offer_ids,
        distances_by_offer_id={
            str(row["id"]): row.get("distance_km")
            for row in rows
            if row.get("id")
        },
        total=int(first.get("total") or 0),
        supermarket_count=int(first.get("supermarket_count") or 0),
        counts_by_supermarket_id=dict(first.get("counts_by_supermarket_id") or {}),
        counts_by_supermarket_slug=dict(first.get("counts_by_supermarket_slug") or {}),
    )


def _nearby_offer_items(
    sb, offer_ids: list[str], distances_by_offer_id: dict[str, float | None]
) -> list[dict]:
    if not offer_ids:
        return []
    response = _public_offers_query(sb, exact_count=False).in_("id", offer_ids).execute()
    offers_by_id = {offer["id"]: offer for offer in _serialize_offers(response.data or [])}
    return [
        {**offers_by_id[offer_id], "distance_km": distances_by_offer_id.get(offer_id)}
        for offer_id in offer_ids
        if offer_id in offers_by_id
    ]


def _request_distances(
    sb, request: Request, user_id: str | None
) -> dict[str, float | None] | None:
    guest_token = request.cookies.get(GUEST_LOCATION_COOKIE) if user_id is None else None
    guest_location = read_guest_location(guest_token)
    if user_id is None and guest_location is None:
        raise guest_location_required(clear_cookie=guest_token is not None)
    location = request_location(sb, user_id, guest_location)
    if location is None:
        return None
    return nearby_supermarket_distances(
        sb, location.municipality_code, location.max_distance_km
    )


@router.get("")
async def list_public_offers(
    q: str | None = Query(None),
    category: str | None = Query(None),
    subcategory: str | None = Query(None),
    supermarket_id: str | None = Query(None),
    supermarket_ids: list[str] = Query(default=[]),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    request: Request = None,
    user_id: str | None = Depends(get_optional_user_id),
) -> dict:
    """Return currently visible offers; offer fields are self-contained."""
    sb = get_supabase()
    distances = _request_distances(sb, request, user_id)
    if distances == {}:
        return {"items": [], "total": 0, "nextPage": None}
    return _public_offers_response(
        sb, q=q, category=category, subcategory=subcategory,
        supermarket_id=supermarket_id, supermarket_ids=supermarket_ids,
        limit=limit, offset=offset, distances_by_supermarket_id=distances,
    )


@router.get("/discovery")
async def discover_public_offers(
    q: str | None = Query(None),
    category: str | None = Query(None),
    subcategory: str | None = Query(None),
    supermarket_id: str | None = Query(None),
    supermarket_ids: list[str] = Query(default=[]),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    request: Request = None,
    user_id: str | None = Depends(get_optional_user_id),
) -> dict:
    """Return first offer page and nearby active branches from one radius lookup."""
    sb = get_supabase()
    distances = _request_distances(sb, request, user_id)
    if distances == {}:
        return {"items": [], "total": 0, "nextPage": None, "supermarkets": []}
    page = _public_offers_response(
        sb, q=q, category=category, subcategory=subcategory,
        supermarket_id=supermarket_id, supermarket_ids=supermarket_ids,
        limit=limit, offset=offset, distances_by_supermarket_id=distances,
    )
    return {
        **page,
        "supermarkets": active_nearby_supermarkets(sb, distances or {}),
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
            "supermarket_municipality": _supermarket_municipality(supermarket),
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
