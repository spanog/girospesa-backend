"""
Greedy set-cover optimization for shopping lists.
Returns the cheapest store combination that covers as many list items as possible.
"""

from __future__ import annotations

import difflib
from typing import Annotated, Literal

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
    mode: Literal["minimize_stores", "maximize_savings"] = "maximize_savings"


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
    offer_id: str
    product_id: str
    product_name: str
    brand: str | None
    format: dict
    format_label: str
    price_offer: float
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
    mode: str


def _match_score(item_name: str, product_name: str) -> float:
    return difflib.SequenceMatcher(None, item_name.lower(), product_name.lower()).ratio()


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


@router.post("")
async def optimize(
    body: OptimizeBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> OptimizationResult:
    """
    Greedy set-cover optimization.
    1. Load list items for the user.
    2. For each item, fuzzy-match against active offers using difflib similarity.
    3. Build coverage matrix (item → best offer per store).
    4. Greedy: pick store with most coverage (ties broken by savings or store count).
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
            mode=body.mode,
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
    )
    all_offers: list[dict] = offers_query.execute().data or []
    offers_by_id = {offer["id"]: offer for offer in all_offers}

    # Build coverage matrix: item_id → list of matched offers
    coverage: dict[str, list[dict]] = {item["id"]: [] for item in unchecked}

    for item in unchecked:
        pinned_offer_id = item.get("pinned_offer_id")
        if pinned_offer_id:
            offer = offers_by_id.get(pinned_offer_id)
            if offer and _is_nearby(offer, nearby_distances):
                coverage[item["id"]].append(_coverage_match(offer, 1.0, nearby_distances))
            continue

        pinned_product_id = item.get("pinned_product_id")
        if pinned_product_id:
            product_offers = [
                offer for offer in all_offers
                if offer.get("product_id") == pinned_product_id
                and _is_nearby(offer, nearby_distances)
            ]
            coverage[item["id"]].extend(
                _coverage_match(offer, 1.0, nearby_distances)
                for offer in product_offers
            )
            continue

        for offer in all_offers:
            if not _is_nearby(offer, nearby_distances):
                continue
            product_info: dict = offer.get("products") or {}
            product_name = product_info.get("name", "")
            score = _match_score(item["name"], product_name)
            if score < 0.5:
                continue

            coverage[item["id"]].append(_coverage_match(offer, score, nearby_distances))

    # Sort matches by price_offer ASC, then score DESC for each item
    for item_id in coverage:
        coverage[item_id].sort(
            key=lambda x: (float(x["offer"].get("price_offer", 0)), -x["score"])
        )

    # Greedy store selection
    store_groups: dict[str, dict] = {}
    assigned: set[str] = set()
    remaining = [item["id"] for item in unchecked]

    while remaining:
        store_scores: dict[str, dict] = {}
        for item_id in remaining:
            seen_stores_for_item: set[str] = set()
            for match in coverage[item_id]:
                store_id = match["store"].get("id")
                if not store_id or store_id in seen_stores_for_item:
                    continue
                seen_stores_for_item.add(store_id)
                if store_id not in store_scores:
                    store_scores[store_id] = {
                        "store": match["store"],
                        "items": [],
                        "total_savings": 0.0,
                    }
                if item_id not in [x["item_id"] for x in store_scores[store_id]["items"]]:
                    store_scores[store_id]["items"].append({
                        "item_id": item_id,
                        "match": match,
                    })
                    store_scores[store_id]["total_savings"] += match["savings"]

        if not store_scores:
            break

        if body.mode == "minimize_stores":
            best_store_id = max(
                store_scores,
                key=lambda sid: (
                    len(store_scores[sid]["items"]),
                    store_scores[sid]["total_savings"],
                ),
            )
        else:  # maximize_savings
            best_store_id = max(
                store_scores,
                key=lambda sid: (
                    store_scores[sid]["total_savings"],
                    len(store_scores[sid]["items"]),
                ),
            )

        best = store_scores[best_store_id]
        if not best["items"]:
            break

        store_info = best["store"]
        if best_store_id not in store_groups:
            store_groups[best_store_id] = {
                "supermarket_id": best_store_id,
                "supermarket_name": store_info.get("name", ""),
                "supermarket_logo_url": store_info.get("logo_url"),
                "distance_km": best["items"][0]["match"]["distance"] if best["items"] else None,
                "products": [],
                "subtotal": 0.0,
                "savings": 0.0,
            }

        for entry in best["items"]:
            item_id = entry["item_id"]
            if item_id in assigned:
                continue
            match = entry["match"]
            offer = match["offer"]
            product_info = match["product_info"]
            item_name = next((i["name"] for i in unchecked if i["id"] == item_id), "")

            alternatives = [
                ProductAlternative(
                    offer_id=alt["offer"]["id"],
                    product_id=alt["offer"]["product_id"],
                    brand=alt["product_info"].get("brand"),
                    name=alt["product_info"].get("name", ""),
                    format=alt["product_info"].get("format") or {},
                    format_label=alt["product_info"].get("format_label") or "",
                    price_offer=float(alt["offer"].get("price_offer", 0)),
                    price_original=(
                        float(alt["offer"]["price_original"])
                        if alt["offer"].get("price_original")
                        else None
                    ),
                    discount_pct=alt["offer"].get("discount_pct"),
                    unit_price=alt["offer"].get("unit_price"),
                    unit_price_value=alt["offer"].get("unit_price_value"),
                    unit_price_unit=alt["offer"].get("unit_price_unit"),
                    unit_price_label=alt["offer"].get("unit_price") or format_unit_price_label(
                        alt["offer"].get("unit_price_value"),
                        alt["offer"].get("unit_price_unit"),
                    ),
                    supermarket_id=alt["store"].get("id", ""),
                    supermarket_name=alt["store"].get("name", ""),
                    valid_to=str(alt["offer"].get("valid_to", "")),
                    is_same_store=alt["store"].get("id") == best_store_id,
                )
                for alt in coverage[item_id]
            ]

            item_quantity = float(
                next((i.get("quantity", 1) for i in unchecked if i["id"] == item_id), 1)
            )

            store_groups[best_store_id]["products"].append(
                MatchedProduct(
                    list_item_id=item_id,
                    list_item_name=item_name,
                    offer_id=offer["id"],
                    product_id=offer["product_id"],
                    product_name=product_info.get("name", ""),
                    brand=product_info.get("brand"),
                    format=product_info.get("format") or {},
                    format_label=product_info.get("format_label") or "",
                    price_offer=float(offer["price_offer"]),
                    price_original=(
                        float(offer["price_original"]) if offer.get("price_original") else None
                    ),
                    discount_pct=offer.get("discount_pct"),
                    unit_price=offer.get("unit_price"),
                    unit_price_value=offer.get("unit_price_value"),
                    unit_price_unit=offer.get("unit_price_unit"),
                    unit_price_label=offer.get("unit_price") or format_unit_price_label(
                        offer.get("unit_price_value"),
                        offer.get("unit_price_unit"),
                    ),
                    quantity=item_quantity,
                    match_score=match["score"],
                    alternatives=alternatives,
                )
            )
            store_groups[best_store_id]["subtotal"] += float(offer["price_offer"]) * item_quantity
            store_groups[best_store_id]["savings"] += match["savings"] * item_quantity
            assigned.add(item_id)
            remaining.remove(item_id)

    unmatched = [
        next(i["name"] for i in unchecked if i["id"] == item_id)
        for item_id in (set(i["id"] for i in unchecked) - assigned)
    ]

    total_cost = sum(sg["subtotal"] for sg in store_groups.values())
    total_savings = sum(sg["savings"] for sg in store_groups.values())
    coverage_pct = int(len(assigned) / len(unchecked) * 100) if unchecked else 100

    return OptimizationResult(
        store_groups=[StoreGroup(**sg) for sg in store_groups.values()],
        total_savings=round(total_savings, 2),
        total_cost=round(total_cost, 2),
        unmatched_items=unmatched,
        coverage_percent=coverage_pct,
        mode=body.mode,
    )
