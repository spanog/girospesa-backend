from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from core.database import get_postgres_cursor, get_supabase, has_direct_postgres
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
_SEARCH_LIMIT = 200


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


def _empty_page() -> dict:
    return {
        "items": [],
        "nextPage": None,
        "total": 0,
        "supermarket_count": 0,
        "expiring_soon_count": 0,
        "counts_by_supermarket_id": {},
        "counts_by_supermarket_slug": {},
    }


def _use_direct_postgres() -> bool:
    try:
        return has_direct_postgres() is True
    except Exception:
        return False


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
    query, *, product_ids, exact_product_id, category, subcategory, supermarket_ids
):
    if product_ids is not None:
        query = query.in_("product_id", product_ids)
    if exact_product_id:
        query = query.eq("product_id", exact_product_id)
    if category:
        query = query.eq("products.category", category)
    if subcategory:
        query = query.eq("products.subcategory", subcategory)
    if supermarket_ids is not None:
        query = query.in_("supermarket_id", supermarket_ids)
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


def _resolve_supermarket_ids(
    *,
    resolved_supermarket_id: str | None,
    supermarket_ids: list[str] | None,
    nearby_ids: list[str] | None,
) -> list[str] | None:
    selected_ids = [
        supermarket_id
        for supermarket_id in (supermarket_ids or [])
        if supermarket_id
    ]
    if resolved_supermarket_id:
        selected_ids.append(resolved_supermarket_id)
    normalized_selected_ids = list(dict.fromkeys(selected_ids))

    if nearby_ids is None:
        return normalized_selected_ids or None
    if not nearby_ids:
        return []
    if not normalized_selected_ids:
        return nearby_ids
    nearby_set = set(nearby_ids)
    return [
        supermarket_id
        for supermarket_id in normalized_selected_ids
        if supermarket_id in nearby_set
    ]


def _build_counts(rows: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    counts_by_id: dict[str, int] = {}
    counts_by_slug: dict[str, int] = {}
    for row in rows:
        supermarket_id = row.get("supermarket_id")
        supermarket_slug = row.get("supermarket_slug")
        if supermarket_id:
            counts_by_id[supermarket_id] = counts_by_id.get(supermarket_id, 0) + 1
        if supermarket_slug:
            counts_by_slug[supermarket_slug] = counts_by_slug.get(supermarket_slug, 0) + 1
    return counts_by_id, counts_by_slug


def _search_score_case(product_ids: list[str], score_map: dict[str, float]) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    for product_id in product_ids:
        clauses.append("WHEN o.product_id = %s::uuid THEN %s::double precision")
        params.extend([product_id, score_map[product_id]])
    if not clauses:
        return "0::double precision", []
    return f"(CASE {' '.join(clauses)} ELSE 0::double precision END)", params


def _list_products_direct_postgres(
    *,
    q: str | None,
    product_id: str | None,
    category: str | None,
    subcategory: str | None,
    supermarket_ids: list[str] | None,
    sort: str | None,
    expiring_soon: bool,
    limit: int,
    offset: int,
    score_map: dict[str, float] | None,
) -> dict:
    product_ids = list(score_map.keys()) if score_map is not None else []
    score_case_sql = "0::double precision"
    score_case_params: list[object] = []
    if product_ids:
        score_case_sql, score_case_params = _search_score_case(product_ids, score_map)

    filters = [
        "o.is_confirmed = true",
        "o.offer_kind = %s",
        "(o.valid_from IS NULL OR o.valid_from <= CURRENT_DATE)",
        "(o.valid_to IS NULL OR o.valid_to >= CURRENT_DATE)",
    ]
    params: list[object] = [_PUBLIC_OFFER_KIND]

    if product_id:
        filters.append("o.product_id = %s::uuid")
        params.append(product_id)
    if category:
        filters.append("p.category = %s")
        params.append(category)
    if subcategory:
        filters.append("p.subcategory = %s")
        params.append(subcategory)
    if supermarket_ids is not None:
        filters.append("o.supermarket_id = ANY(%s::uuid[])")
        params.append(supermarket_ids)
    if q:
        if not product_ids:
            return _empty_page()
        filters.append("o.product_id = ANY(%s::uuid[])")
        params.append(product_ids)
    if expiring_soon:
        filters.append("o.valid_to >= CURRENT_DATE")
        filters.append("o.valid_to <= CURRENT_DATE + INTERVAL '3 day'")

    where_sql = " AND ".join(filters)
    order_sql = (
        "search_score DESC, name ASC"
        if q
        else "valid_to ASC NULLS LAST, name ASC"
        if sort == "expiry"
        else "name ASC"
    )

    with get_postgres_cursor() as cursor:
        cursor.execute(
            f"""
            WITH filtered AS (
              SELECT
                o.id,
                o.product_id,
                o.flyer_id,
                o.supermarket_id,
                o.supermarket_name,
                o.format,
                o.format_label,
                o.price_original,
                o.price_offer,
                o.discount_pct,
                o.unit_price,
                o.unit_price_value,
                o.unit_price_unit,
                o.offer_type,
                o.offer_notes,
                o.valid_from,
                o.valid_to,
                o.raw_text,
                o.confidence_score,
                o.created_at,
                p.name,
                p.brand,
                p.category,
                p.subcategory,
                p.image_url AS product_image_url,
                s.logo_url AS supermarket_logo_url,
                s.slug AS supermarket_slug,
                s.address,
                s.city,
                {score_case_sql} AS search_score
              FROM public.offers o
              JOIN public.products p ON p.id = o.product_id
              LEFT JOIN public.supermarkets s ON s.id = o.supermarket_id
              WHERE {where_sql}
            ),
            paged AS (
              SELECT
                *,
                COUNT(*) OVER()::int AS total_count
              FROM filtered
              ORDER BY {order_sql}
              LIMIT %s
              OFFSET %s
            )
            SELECT *
            FROM paged
            ORDER BY {order_sql}
            """,
            [*score_case_params, *params, limit, offset],
        )
        rows = cursor.fetchall()

        items = [
            {
                "id": str(row["id"]),
                "product_id": str(row["product_id"]) if row.get("product_id") else None,
                "flyer_id": row.get("flyer_id"),
                "supermarket_id": str(row["supermarket_id"]) if row.get("supermarket_id") else None,
                "supermarket_name": row.get("supermarket_name") or "",
                "supermarket_logo_url": row.get("supermarket_logo_url"),
                "supermarket_slug": row.get("supermarket_slug"),
                "supermarket_address": _format_supermarket_address(
                    {"address": row.get("address"), "city": row.get("city")}
                ),
                "name": row.get("name", ""),
                "brand": row.get("brand"),
                "category": row.get("category"),
                "subcategory": row.get("subcategory"),
                "format": row.get("format") or {},
                "format_label": row.get("format_label") or "",
                "price_original": row.get("price_original"),
                "price_offer": row.get("price_offer"),
                "discount_pct": row.get("discount_pct"),
                "unit_price": row.get("unit_price"),
                "unit_price_value": row.get("unit_price_value"),
                "unit_price_unit": row.get("unit_price_unit"),
                "unit_price_label": row.get("unit_price")
                or format_unit_price_label(row.get("unit_price_value"), row.get("unit_price_unit")),
                "offer_type": row.get("offer_type"),
                "offer_notes": row.get("offer_notes"),
                "valid_from": row.get("valid_from"),
                "valid_to": row.get("valid_to"),
                "image_url": row.get("product_image_url"),
                "raw_text": row.get("raw_text"),
                "confidence_score": row.get("confidence_score"),
                "created_at": row.get("created_at"),
            }
            for row in rows
        ]

        total = rows[0]["total_count"] if rows else 0
        next_page = (offset // limit) + 1 if offset + limit < total else None

        counts_by_id: dict[str, int] = {}
        counts_by_slug: dict[str, int] = {}
        supermarket_count = 0
        expiring_soon_count = 0
        if offset == 0:
            cursor.execute(
                f"""
                WITH filtered AS (
                  SELECT
                    o.supermarket_id,
                    s.slug AS supermarket_slug,
                    o.valid_to
                  FROM public.offers o
                  JOIN public.products p ON p.id = o.product_id
                  LEFT JOIN public.supermarkets s ON s.id = o.supermarket_id
                  WHERE {where_sql}
                )
                SELECT supermarket_id, supermarket_slug, valid_to
                FROM filtered
                """,
                params,
            )
            count_rows = cursor.fetchall()
            count_dict_rows = [
                {
                    "supermarket_id": str(row["supermarket_id"]) if row.get("supermarket_id") else None,
                    "supermarket_slug": row.get("supermarket_slug"),
                    "valid_to": row.get("valid_to"),
                }
                for row in count_rows
            ]
            counts_by_id, counts_by_slug = _build_counts(count_dict_rows)
            supermarket_count = len(counts_by_id)
            today = date.today()
            cutoff = today + timedelta(days=3)
            expiring_soon_count = sum(
                1
                for row in count_dict_rows
                if row.get("valid_to") is not None and today <= row["valid_to"] <= cutoff
            )

    return {
        "items": items,
        "nextPage": next_page,
        "total": total,
        "supermarket_count": supermarket_count,
        "expiring_soon_count": expiring_soon_count,
        "counts_by_supermarket_id": counts_by_id,
        "counts_by_supermarket_slug": counts_by_slug,
    }


@router.get("")
async def list_products(
    q: str | None = Query(None, description="Full-text search query"),
    product_id: str | None = Query(None, description="Exact canonical product id"),
    category: str | None = Query(None),
    subcategory: str | None = Query(None),
    supermarket: str | None = Query(None, description="Supermarket slug"),
    supermarket_id: str | None = Query(None, description="Exact supermarket id"),
    supermarket_ids: list[str] | None = Query(
        None,
        description="Exact supermarket ids for multi-store filtering",
    ),
    lat: float | None = Query(None, description="User latitude for distance filtering"),
    lng: float | None = Query(None, description="User longitude for distance filtering"),
    max_distance_km: float = Query(10.0, gt=0, le=20, description="Max supermarket distance in km"),
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

    resolved_supermarket_id = supermarket_id
    if resolved_supermarket_id is None and supermarket:
        resolved_supermarket_id = _resolve_supermarket_id(sb, supermarket)
        if not resolved_supermarket_id:
            return _empty_page()

    nearby_ids: list[str] | None = None
    if lat is not None and lng is not None:
        nearby_ids = _nearby_supermarket_ids(sb, lat, lng, max_distance_km)
        if not nearby_ids:
            return _empty_page()

    filtered_supermarket_ids = _resolve_supermarket_ids(
        resolved_supermarket_id=resolved_supermarket_id,
        supermarket_ids=supermarket_ids,
        nearby_ids=nearby_ids,
    )
    if filtered_supermarket_ids == []:
        return _empty_page()

    score_map = _search_product_scores(sb, q)
    if score_map == {}:
        return _empty_page()

    if _use_direct_postgres():
        return _list_products_direct_postgres(
            q=q,
            product_id=product_id,
            category=category,
            subcategory=subcategory,
            supermarket_ids=filtered_supermarket_ids,
            sort=sort,
            expiring_soon=expiring_soon,
            limit=limit,
            offset=offset,
            score_map=score_map,
        )

    product_ids = list(score_map.keys()) if score_map is not None else None

    filter_kwargs = dict(
        product_ids=product_ids,
        exact_product_id=product_id,
        category=category,
        subcategory=subcategory,
        supermarket_ids=filtered_supermarket_ids,
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
    counts_by_supermarket_id: dict[str, int] = {}
    counts_by_supermarket_slug: dict[str, int] = {}
    if offset == 0:
        sc_query = _apply_offer_filters(
            apply_current_offer_window(
                sb.table("offers")
                .select("supermarket_id, supermarkets(slug), products!inner(id)")
                .eq("is_confirmed", True)
                .eq("offer_kind", _PUBLIC_OFFER_KIND)
            ),
            **filter_kwargs,
        )
        sc_resp = sc_query.execute()
        sc_rows = [
            {
                "supermarket_id": row.get("supermarket_id"),
                "supermarket_slug": (row.get("supermarkets") or {}).get("slug"),
            }
            for row in (sc_resp.data or [])
        ]
        counts_by_supermarket_id, counts_by_supermarket_slug = _build_counts(sc_rows)
        supermarket_count = len(counts_by_supermarket_id)

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

    return {
        "items": items,
        "nextPage": next_page,
        "total": total,
        "supermarket_count": supermarket_count,
        "expiring_soon_count": expiring_soon_count,
        "counts_by_supermarket_id": counts_by_supermarket_id,
        "counts_by_supermarket_slug": counts_by_supermarket_slug,
    }


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
