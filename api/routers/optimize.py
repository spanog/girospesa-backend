"""
Single-plan offer matching for shopping lists.
Returns best offer per item, grouped by supermarket with selectable alternatives.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.database import get_supabase
from services.extraction.normalizer import format_unit_price_label
from services.offer_visibility import apply_current_offer_window

router = APIRouter()


def _verify_member(sb: object, list_id: str, user_id: str) -> None:
    result = (
        sb.table("list_members")  # type: ignore[union-attr,attr-defined]
        .select("id")
        .eq("list_id", list_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=403, detail="Not a member of this list")


class OptimizeBody(BaseModel):
    list_id: str


class ProductAlternative(BaseModel):
    offer_id: str
    product_id: str
    brand: str | None
    name: str
    format: dict
    format_label: str
    price_offer: float
    price_original: float | None
    discount_pct: int | None
    unit_price: str | None = None
    unit_price_value: float | None = None
    unit_price_unit: str | None = None
    unit_price_label: str | None = None
    supermarket_id: str
    supermarket_name: str
    valid_to: str
    is_same_store: bool


class MatchedProduct(BaseModel):
    list_item_id: str
    list_item_name: str
    source: str = "offer"
    offer_id: str | None
    product_id: str | None
    product_name: str
    brand: str | None
    format: dict
    format_label: str
    price_offer: float | None
    price_original: float | None
    discount_pct: int | None
    unit_price: str | None = None
    unit_price_value: float | None = None
    unit_price_unit: str | None = None
    unit_price_label: str | None = None
    quantity: float = 1.0
    match_score: float
    alternatives: list[ProductAlternative]


class StoreGroup(BaseModel):
    supermarket_id: str
    supermarket_name: str
    supermarket_logo_url: str | None
    distance_km: float | None
    products: list[MatchedProduct]
    subtotal: float
    savings: float


class OptimizationResult(BaseModel):
    store_groups: list[StoreGroup]
    total_savings: float
    total_cost: float
    unmatched_items: list[str]
    coverage_percent: int


def _pick_search_coordinate(profile: dict, search_key: str, home_key: str) -> float | None:
    return profile.get(search_key) if profile.get(search_key) is not None else profile.get(home_key)


def _nearby_distances(sb, lat: float, lng: float, max_distance_km: float) -> dict[str, float]:
    response = sb.rpc(
        "nearby_supermarkets",
        {
            "user_lat": lat,
            "user_lng": lng,
            "radius_m": max_distance_km * 1000,
        },
    ).execute()
    return {row["id"]: row["distance_km"] for row in (response.data or [])}


def _offer_select() -> str:
    return (
        "id, product_id, price_original, price_offer, discount_pct, "
        "unit_price, unit_price_value, unit_price_unit, valid_to,"
        " products(id, name, brand, format, format_label),"
        " supermarkets(id, name, logo_url)"
    )


def _offer_distance(match: dict, nearby_distances: dict[str, float] | None) -> float | None:
    store_id = (match.get("supermarkets") or {}).get("id")
    if nearby_distances is None:
        return None
    return nearby_distances.get(store_id)


def _is_nearby(offer: dict, nearby_distances: dict[str, float] | None) -> bool:
    if nearby_distances is None:
        return True
    store_id = (offer.get("supermarkets") or {}).get("id")
    return store_id in nearby_distances


def _offer_savings(offer: dict) -> float:
    if not offer.get("price_original"):
        return 0.0
    return float(offer["price_original"]) - float(offer["price_offer"])


def _coverage_match(
    offer: dict,
    score: float,
    nearby_distances: dict[str, float] | None,
) -> dict:
    return {
        "offer": offer,
        "product_info": offer.get("products") or {},
        "store": offer.get("supermarkets") or {},
        "score": score,
        "savings": _offer_savings(offer),
        "distance": _offer_distance(offer, nearby_distances),
    }


def _semantic_product_scores(sb: object, item_name: str, limit: int = 12) -> dict[str, float]:
    rows = sb.rpc(  # type: ignore[union-attr,attr-defined]
        "search_products_catalog",
        {"query": item_name, "lim": limit},
    ).execute().data or []
    return {row["id"]: float(row.get("score") or 0) for row in rows}


def _semantic_offer_matches(
    sb: object,
    item: dict,
    all_offers: list[dict],
    nearby_distances: dict[str, float] | None,
) -> list[dict]:
    scores = _semantic_product_scores(sb, item["name"])
    matches = [
        _coverage_match(offer, scores[offer["product_id"]], nearby_distances)
        for offer in all_offers
        if offer.get("product_id") in scores and _is_nearby(offer, nearby_distances)
    ]
    return sorted(matches, key=lambda x: (float(x["offer"].get("price_offer", 0)), -x["score"]))


def _product_offer_matches(
    product_id: str,
    all_offers: list[dict],
    nearby_distances: dict[str, float] | None,
) -> list[dict]:
    matches = [
        _coverage_match(offer, 1.0, nearby_distances)
        for offer in all_offers
        if offer.get("product_id") == product_id and _is_nearby(offer, nearby_distances)
    ]
    return sorted(matches, key=lambda x: float(x["offer"].get("price_offer", 0)))


def _dedupe_matches(matches: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for match in matches:
        offer_id = match["offer"].get("id")
        if offer_id in seen:
            continue
        seen.add(offer_id)
        result.append(match)
    return result


def _item_matches(
    sb: object,
    item: dict,
    all_offers: list[dict],
    offers_by_id: dict[str, dict],
    nearby_distances: dict[str, float] | None,
) -> list[dict]:
    pinned_offer = offers_by_id.get(item.get("pinned_offer_id"))
    primary = []
    if pinned_offer and _is_nearby(pinned_offer, nearby_distances):
        primary = [_coverage_match(pinned_offer, 1.0, nearby_distances)]

    product_matches = []
    if item.get("pinned_product_id"):
        product_matches = _product_offer_matches(
            item["pinned_product_id"], all_offers, nearby_distances
        )

    semantic_matches = _semantic_offer_matches(sb, item, all_offers, nearby_distances)
    return _dedupe_matches(primary + product_matches + semantic_matches)


def _product_alternative(match: dict, selected_store_id: str) -> ProductAlternative:
    offer = match["offer"]
    product_info = match["product_info"]
    store = match["store"]
    return ProductAlternative(
        offer_id=offer["id"],
        product_id=offer["product_id"],
        brand=product_info.get("brand"),
        name=product_info.get("name", ""),
        format=product_info.get("format") or {},
        format_label=product_info.get("format_label") or "",
        price_offer=float(offer.get("price_offer", 0)),
        price_original=float(offer["price_original"]) if offer.get("price_original") else None,
        discount_pct=offer.get("discount_pct"),
        unit_price=offer.get("unit_price"),
        unit_price_value=offer.get("unit_price_value"),
        unit_price_unit=offer.get("unit_price_unit"),
        unit_price_label=offer.get("unit_price") or format_unit_price_label(
            offer.get("unit_price_value"), offer.get("unit_price_unit")
        ),
        supermarket_id=store.get("id", ""),
        supermarket_name=store.get("name", ""),
        valid_to=str(offer.get("valid_to") or ""),
        is_same_store=store.get("id") == selected_store_id,
    )


def _matched_product(item: dict, match: dict, matches: list[dict]) -> MatchedProduct:
    offer = match["offer"]
    product_info = match["product_info"]
    store_id = match["store"].get("id", "")
    alternatives = [_product_alternative(alt, store_id) for alt in matches]
    return MatchedProduct(
        list_item_id=item["id"],
        list_item_name=item["name"],
        source="offer",
        offer_id=offer["id"],
        product_id=offer["product_id"],
        product_name=product_info.get("name", ""),
        brand=product_info.get("brand"),
        format=product_info.get("format") or {},
        format_label=product_info.get("format_label") or "",
        price_offer=float(offer["price_offer"]),
        price_original=float(offer["price_original"]) if offer.get("price_original") else None,
        discount_pct=offer.get("discount_pct"),
        unit_price=offer.get("unit_price"),
        unit_price_value=offer.get("unit_price_value"),
        unit_price_unit=offer.get("unit_price_unit"),
        unit_price_label=offer.get("unit_price") or format_unit_price_label(
            offer.get("unit_price_value"), offer.get("unit_price_unit")
        ),
        quantity=float(item.get("quantity", 1)),
        match_score=match["score"],
        alternatives=alternatives,
    )


def _manual_product(item: dict, matches: list[dict]) -> MatchedProduct:
    return MatchedProduct(
        list_item_id=item["id"],
        list_item_name=item["name"],
        source="manual",
        offer_id=None,
        product_id=None,
        product_name=item["name"],
        brand=None,
        format={},
        format_label="",
        price_offer=None,
        price_original=None,
        discount_pct=None,
        quantity=float(item.get("quantity", 1)),
        match_score=1.0,
        alternatives=[_product_alternative(alt, "__manual__") for alt in matches],
    )


def _empty_store_group(match: dict) -> dict:
    store = match["store"]
    return {
        "supermarket_id": store.get("id", ""),
        "supermarket_name": store.get("name", ""),
        "supermarket_logo_url": store.get("logo_url"),
        "distance_km": match["distance"],
        "products": [],
        "subtotal": 0.0,
        "savings": 0.0,
    }


def _manual_store_group() -> dict:
    return {
        "supermarket_id": "__manual__",
        "supermarket_name": "Senza offerta",
        "supermarket_logo_url": None,
        "distance_km": None,
        "products": [],
        "subtotal": 0.0,
        "savings": 0.0,
    }


@router.post("")
async def optimize(
    body: OptimizeBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> OptimizationResult:
    """
    Build one shopping route plan.
    1. Load list items for the user.
    2. Keep pinned offers as defaults when still active and nearby.
    3. Fuzzy-match manual item names through pg_trgm product search.
    4. Pick best offer per item and expose alternatives ordered by price.
    """
    sb = get_supabase()
    _verify_member(sb, body.list_id, user_id)

    # Fetch list items
    list_resp = (
        sb.table("shopping_lists")
        .select("items")
        .eq("id", body.list_id)
        .single()
        .execute()
    )
    items: list[dict] = list_resp.data["items"]
    unchecked = [i for i in items if not i.get("checked") and not i.get("purchased")]

    if not unchecked:
        return OptimizationResult(
            store_groups=[],
            total_savings=0,
            total_cost=0,
            unmatched_items=[],
            coverage_percent=100,
        )

    # Fetch user profile for location — may not exist for new users
    profile_resp = (
        sb.table("user_profiles")
        .select("home_lat, home_lng, search_lat, search_lng, max_distance_km")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    profile: dict = (profile_resp.data if profile_resp is not None else None) or {}

    user_lat = _pick_search_coordinate(profile, "search_lat", "home_lat")
    user_lng = _pick_search_coordinate(profile, "search_lng", "home_lng")
    max_km = profile.get("max_distance_km") or 10
    nearby_distances = None
    if user_lat is not None and user_lng is not None:
        nearby_distances = _nearby_distances(sb, user_lat, user_lng, max_km)

    # Fetch all active offers joined with product catalog and supermarket info
    offers_query = apply_current_offer_window(
        sb.table("offers").select(_offer_select())
    ).eq("is_confirmed", True)
    all_offers: list[dict] = offers_query.execute().data or []
    offers_by_id = {offer["id"]: offer for offer in all_offers}

    store_groups: dict[str, dict] = {}
    unmatched: list[str] = []

    for item in unchecked:
        matches = _item_matches(sb, item, all_offers, offers_by_id, nearby_distances)
        is_manual = not item.get("pinned_offer_id") and not item.get("pinned_product_id")
        if is_manual:
            if "__manual__" not in store_groups:
                store_groups["__manual__"] = _manual_store_group()
            store_groups["__manual__"]["products"].append(_manual_product(item, matches))
            continue

        if not matches:
            unmatched.append(item["name"])
            continue

        match = matches[0]
        store_id = match["store"].get("id")
        if not store_id:
            unmatched.append(item["name"])
            continue

        if store_id not in store_groups:
            store_groups[store_id] = _empty_store_group(match)

        item_quantity = float(item.get("quantity", 1))
        store_groups[store_id]["products"].append(_matched_product(item, match, matches))
        store_groups[store_id]["subtotal"] += float(match["offer"]["price_offer"]) * item_quantity
        store_groups[store_id]["savings"] += match["savings"] * item_quantity

    total_cost = sum(sg["subtotal"] for sg in store_groups.values())
    total_savings = sum(sg["savings"] for sg in store_groups.values())
    matched_count = len(unchecked) - len(unmatched)
    coverage_pct = int(matched_count / len(unchecked) * 100) if unchecked else 100

    return OptimizationResult(
        store_groups=[StoreGroup(**sg) for sg in store_groups.values()],
        total_savings=round(total_savings, 2),
        total_cost=round(total_cost, 2),
        unmatched_items=unmatched,
        coverage_percent=coverage_pct,
    )
