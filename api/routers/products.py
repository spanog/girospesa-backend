from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from core.database import get_supabase
from services.extraction.normalizer import format_unit_price_label

_OFFER_PRODUCT_SELECT = (
    "*, "
    "products(id, name, brand, category, subcategory, format, image_url), "
    "supermarkets(name, slug, logo_url, color_hex)"
)
_OFFER_PRODUCT_LIST_SELECT = (
    "*, "
    "products!inner(id, name, brand, category, subcategory, format, image_url), "
    "supermarkets(name, slug, logo_url, color_hex)"
)


def _flatten_offer(offer: dict) -> dict:
    """Merge nested products/supermarkets dicts into a flat response dict."""
    offer = dict(offer)
    product = offer.pop("products") or {}
    supermarket = offer.pop("supermarkets") or {}
    return {
        **offer,
        "product_id": product.get("id"),
        "name": product.get("name", ""),
        "brand": product.get("brand"),
        "category": product.get("category"),
        "subcategory": product.get("subcategory"),
        "format": product.get("format"),
        "image_url": product.get("image_url"),
        "supermarket_name": supermarket.get("name") or offer.get("supermarket_name", ""),
        "supermarket_logo_url": supermarket.get("logo_url"),
        "supermarket_slug": supermarket.get("slug"),
        "unit_price_label": offer.get("unit_price") or format_unit_price_label(
            offer.get("unit_price_value"),
            offer.get("unit_price_unit"),
        ),
    }


def _nearby_supermarket_ids(sb, lat: float, lng: float, max_distance_km: float) -> list[str]:
    response = sb.rpc(
        "nearby_supermarkets",
        {
            "user_lat": lat,
            "user_lng": lng,
            "radius_m": max_distance_km * 1000,
        },
    ).execute()
    return [row["id"] for row in (response.data or [])]


router = APIRouter()


def _resolve_supermarket_id(sb, slug: str) -> str | None:
    resp = (
        sb.table("supermarkets")
        .select("id")
        .eq("slug", slug)
        .maybe_single()
        .execute()
    )
    return resp.data["id"] if resp.data else None


def _apply_offer_filters(query, *, q, category, subcategory, supermarket_id, nearby_ids):
    if q:
        query = query.text_search("products.name_tsv", q, config="italian")
    if category:
        query = query.eq("products.category", category)
    if subcategory:
        query = query.eq("products.subcategory", subcategory)
    if supermarket_id:
        query = query.eq("supermarket_id", supermarket_id)
    if nearby_ids is not None:
        query = query.in_("supermarket_id", nearby_ids)
    return query


_SORT_OPTIONS: dict[str, tuple[str, bool]] = {
    "discount": ("discount_pct", True),
    "expiry": ("valid_to", False),
    "default": ("discount_pct", True),
}


@router.get("")
async def list_products(
    q: str | None = Query(None, description="Full-text search query"),
    category: str | None = Query(None),
    subcategory: str | None = Query(None),
    supermarket: str | None = Query(None, description="Supermarket slug"),
    lat: float | None = Query(None, description="User latitude for distance filtering"),
    lng: float | None = Query(None, description="User longitude for distance filtering"),
    max_distance_km: float = Query(10.0, gt=0, le=100, description="Max supermarket distance in km"),
    sort: str | None = Query(None, description="Sort mode: discount | expiry"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
) -> dict:
    """
    List active product offers.
    Accessible to all users (anon + authenticated) — RLS handles visibility.
    """
    sb = get_supabase()

    supermarket_id: str | None = None
    if supermarket:
        supermarket_id = _resolve_supermarket_id(sb, supermarket)
        if not supermarket_id:
            return {"items": [], "nextPage": None, "total": 0, "supermarket_count": 0}

    nearby_ids: list[str] | None = None
    if lat is not None and lng is not None:
        nearby_ids = _nearby_supermarket_ids(sb, lat, lng, max_distance_km)
        if not nearby_ids:
            return {"items": [], "nextPage": None, "total": 0, "supermarket_count": 0}

    filter_kwargs = dict(q=q, category=category, subcategory=subcategory, supermarket_id=supermarket_id, nearby_ids=nearby_ids)

    sort_col, sort_desc = _SORT_OPTIONS.get(sort or "default", _SORT_OPTIONS["default"])
    nullsfirst = not sort_desc  # PostgREST Python client uses `nullsfirst`.

    base_query = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_LIST_SELECT, count="exact")
        .eq("is_active", True)
        .eq("is_confirmed", True)
        .order(sort_col, desc=sort_desc, nullsfirst=nullsfirst)
    )
    query = _apply_offer_filters(base_query, **filter_kwargs).range(offset, offset + limit - 1)
    response = query.execute()

    items = [_flatten_offer(offer) for offer in (response.data or [])]
    total = response.count or 0
    next_page = (offset // limit) + 1 if offset + limit < total else None

    # Compute supermarket_count only on first page to avoid redundant work on subsequent pages
    supermarket_count = 0
    if offset == 0:
        sc_query = _apply_offer_filters(
            sb.table("offers").select("supermarket_id").eq("is_active", True).eq("is_confirmed", True),
            **filter_kwargs,
        )
        sc_resp = sc_query.execute()
        supermarket_count = len({row["supermarket_id"] for row in (sc_resp.data or [])})

    return {"items": items, "nextPage": next_page, "total": total, "supermarket_count": supermarket_count}


@router.get("/{product_id}")
async def get_product(product_id: str) -> dict:
    """
    Get a single offer by its ID, with joined product and supermarket data.
    Accessible to all users — RLS on offers handles visibility.
    """
    sb = get_supabase()
    resp = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_SELECT)
        .eq("id", product_id)
        .eq("is_confirmed", True)
        .single()
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Offerta non trovata")
    return _flatten_offer(resp.data)


@router.get("/{product_id}/similar")
async def get_similar_products(product_id: str) -> list[dict]:
    """
    Return other active offers for the same canonical product, ordered by price (asc).
    The current offer and its supermarket are excluded from the results.
    """
    sb = get_supabase()

    # Resolve the canonical product_id and current supermarket
    ref_resp = (
        sb.table("offers")
        .select("product_id, supermarket_id")
        .eq("id", product_id)
        .single()
        .execute()
    )
    if not ref_resp.data:
        return []

    canonical_product_id: str = ref_resp.data["product_id"]
    current_supermarket_id: str = ref_resp.data["supermarket_id"]

    similar_resp = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_SELECT)
        .eq("product_id", canonical_product_id)
        .eq("is_active", True)
        .eq("is_confirmed", True)
        .neq("id", product_id)
        .neq("supermarket_id", current_supermarket_id)
        .order("price_offer", desc=False)
        .limit(6)
        .execute()
    )
    return [_flatten_offer(o) for o in (similar_resp.data or [])]
