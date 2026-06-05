from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from core.database import get_supabase
from services.extraction.normalizer import format_unit_price_label
from services.offer_visibility import apply_current_offer_window

_OFFER_PRODUCT_SELECT = (
    "*, "
    "products(id, name, brand, category, subcategory, image_url), "
    "supermarkets(name, slug, logo_url, color_hex, address, city)"
)
_OFFER_PRODUCT_LIST_SELECT = (
    "*, "
    "products!inner(id, name, brand, category, subcategory, image_url), "
    "supermarkets(name, slug, logo_url, color_hex, address, city)"
)
_PUBLIC_OFFER_KIND = "published_target"


def _format_supermarket_address(supermarket: dict) -> str | None:
    parts = [part for part in (supermarket.get("address"), supermarket.get("city")) if part]
    return ", ".join(parts) if parts else None


def _first_row(response) -> dict | None:
    rows = response.data or []
    return rows[0] if rows else None


def _flatten_offer(offer: dict) -> dict:
    """Merge nested products/supermarkets dicts into a flat response dict."""
    offer = dict(offer)
    product = offer.pop("products") or {}
    supermarket = offer.pop("supermarkets") or {}
    return {
        **offer,  # includes format, format_key, format_label from offers table
        "product_id": product.get("id"),
        "name": product.get("name", ""),
        "brand": product.get("brand"),
        "category": product.get("category"),
        "subcategory": product.get("subcategory"),
        "image_url": product.get("image_url"),
        "supermarket_name": supermarket.get("name") or offer.get("supermarket_name", ""),
        "supermarket_logo_url": supermarket.get("logo_url"),
        "supermarket_slug": supermarket.get("slug"),
        "supermarket_address": _format_supermarket_address(supermarket),
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


def _search_product_scores(sb, q: str | None, limit: int = 200) -> dict[str, float] | None:
    if not q:
        return None
    rows = sb.rpc("search_products_catalog", {"query": q, "lim": limit}).execute().data or []
    if not rows:
        return {}
    return {row["id"]: row["score"] for row in rows}


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


def _apply_offer_filters(
    query, *, product_ids, category, subcategory, supermarket_id, nearby_ids
):
    if product_ids is not None:
        query = query.in_("product_id", product_ids)
    if category:
        query = query.eq("products.category", category)
    if subcategory:
        query = query.eq("products.subcategory", subcategory)
    if supermarket_id:
        query = query.eq("supermarket_id", supermarket_id)
    if nearby_ids is not None:
        query = query.in_("supermarket_id", nearby_ids)
    return query


def _apply_expiring_soon_filter(query, *, today: date) -> object:
    cutoff = today + timedelta(days=3)
    return query.gte("valid_to", today.isoformat()).lte("valid_to", cutoff.isoformat())


def _apply_offer_sort(query, *, sort: str | None):
    if sort == "expiry":
        return (
            query.order("valid_to", desc=False, nullsfirst=False)
            .order("name", desc=False, foreign_table="products")
        )
    return query.order("name", desc=False, foreign_table="products")


@router.get("")
async def list_products(
    q: str | None = Query(None, description="Full-text search query"),
    category: str | None = Query(None),
    subcategory: str | None = Query(None),
    supermarket: str | None = Query(None, description="Supermarket slug"),
    lat: float | None = Query(None, description="User latitude for distance filtering"),
    lng: float | None = Query(None, description="User longitude for distance filtering"),
    max_distance_km: float = Query(10.0, gt=0, le=100, description="Max supermarket distance in km"),
    sort: str | None = Query(None, description="Sort mode: expiry"),
    expiring_soon: bool = Query(False, description="Only offers expiring within 3 days"),
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
            return {"items": [], "nextPage": None, "total": 0, "supermarket_count": 0, "expiring_soon_count": 0}

    nearby_ids: list[str] | None = None
    if lat is not None and lng is not None:
        nearby_ids = _nearby_supermarket_ids(sb, lat, lng, max_distance_km)
        if not nearby_ids:
            return {"items": [], "nextPage": None, "total": 0, "supermarket_count": 0, "expiring_soon_count": 0}

    score_map = _search_product_scores(sb, q)
    if score_map == {}:
        return {"items": [], "nextPage": None, "total": 0, "supermarket_count": 0, "expiring_soon_count": 0}
    product_ids = list(score_map.keys()) if score_map is not None else None

    filter_kwargs = dict(
        product_ids=product_ids,
        category=category,
        subcategory=subcategory,
        supermarket_id=supermarket_id,
        nearby_ids=nearby_ids,
    )
    today = date.today()

    base_query = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_LIST_SELECT, count="exact")
        .eq("is_confirmed", True)
        .eq("offer_kind", _PUBLIC_OFFER_KIND)
    )
    if score_map is None:
        # Apply DB sort early (original chain order preserved for non-search path)
        base_query = _apply_offer_sort(base_query, sort=sort)
    base_query = apply_current_offer_window(base_query)
    filtered_query = _apply_offer_filters(base_query, **filter_kwargs)
    if expiring_soon:
        filtered_query = _apply_expiring_soon_filter(filtered_query, today=today)

    if score_map is not None:
        # Fetch all matching offers (up to 200 products × ~10 supermarkets), sort by search score in Python, then paginate
        response = filtered_query.limit(2000).execute()
        items = [_flatten_offer(offer) for offer in (response.data or [])]
        items.sort(key=lambda item: (-score_map.get(item.get("product_id") or "", 0), item.get("name") or ""))
        total = len(items)
        items = items[offset: offset + limit]
    else:
        response = filtered_query.range(offset, offset + limit - 1).execute()
        items = [_flatten_offer(offer) for offer in (response.data or [])]
        total = response.count or 0

    next_page = (offset // limit) + 1 if offset + limit < total else None

    # Compute supermarket_count and expiring_soon_count only on first page
    supermarket_count = 0
    expiring_soon_count = 0
    if offset == 0:
        sc_query = _apply_offer_filters(
            apply_current_offer_window(
                sb.table("offers")
                .select("supermarket_id, products!inner(id)")
                .eq("is_confirmed", True)
                .eq("offer_kind", _PUBLIC_OFFER_KIND)
            ),
            **filter_kwargs,
        )
        sc_resp = sc_query.execute()
        supermarket_count = len({row["supermarket_id"] for row in (sc_resp.data or [])})

        es_query = (
            sb.table("offers")
            .select("id, products!inner(id)", count="exact")
            .eq("is_confirmed", True)
            .eq("offer_kind", _PUBLIC_OFFER_KIND)
        )
        es_query = apply_current_offer_window(es_query, today=today)
        es_query = _apply_offer_filters(es_query, **filter_kwargs)
        es_query = _apply_expiring_soon_filter(es_query, today=today)
        es_resp = es_query.execute()
        expiring_soon_count = es_resp.count or 0

    return {"items": items, "nextPage": next_page, "total": total, "supermarket_count": supermarket_count, "expiring_soon_count": expiring_soon_count}


@router.get("/{product_id}")
async def get_product(product_id: str) -> dict:
    """
    Get a single offer by its ID, with joined product and supermarket data.
    Accessible to all users — RLS on offers handles visibility.
    """
    sb = get_supabase()
    resp = (
        apply_current_offer_window(
            sb.table("offers")
            .select(_OFFER_PRODUCT_SELECT)
            .eq("id", product_id)
            .eq("is_confirmed", True)
            .eq("offer_kind", _PUBLIC_OFFER_KIND)
        )
        .limit(1)
        .execute()
    )
    offer = _first_row(resp)
    if not offer:
        raise HTTPException(status_code=404, detail="Offerta non trovata")
    return _flatten_offer(offer)


@router.get("/{product_id}/similar")
async def get_similar_products(product_id: str) -> list[dict]:
    """
    Return other active offers for the same canonical product, ordered by price (asc).
    The current offer and its supermarket are excluded from the results.
    """
    sb = get_supabase()

    # Resolve the canonical product_id and current supermarket
    ref_resp = (
        apply_current_offer_window(
            sb.table("offers")
            .select("product_id, supermarket_id")
            .eq("id", product_id)
            .eq("is_confirmed", True)
            .eq("offer_kind", _PUBLIC_OFFER_KIND)
        )
        .limit(1)
        .execute()
    )
    reference_offer = _first_row(ref_resp)
    if not reference_offer:
        return []

    canonical_product_id: str = reference_offer["product_id"]
    current_supermarket_id: str = reference_offer["supermarket_id"]

    similar_resp = (
        apply_current_offer_window(
            sb.table("offers")
            .select(_OFFER_PRODUCT_SELECT)
            .eq("product_id", canonical_product_id)
            .eq("is_confirmed", True)
            .eq("offer_kind", _PUBLIC_OFFER_KIND)
            .neq("id", product_id)
            .neq("supermarket_id", current_supermarket_id)
            .order("price_offer", desc=False)
        )
        .limit(6)
        .execute()
    )
    return [_flatten_offer(o) for o in (similar_resp.data or [])]
