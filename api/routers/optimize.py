from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.database import get_supabase
from services.list_offer_visibility import visible_supermarket_ids_for_user
from services.extraction.normalizer import format_unit_price_label
from services.offer_visibility import apply_current_offer_window
from services.repositories import lists_repository as lists_repo

router = APIRouter()
_PUBLIC_OFFER_KIND = "published_target"

_OFFER_SELECT = (
    "id, product_id, supermarket_id, supermarket_name, price_offer, "
    "price_original, unit_price, unit_price_value, unit_price_unit, valid_to, "
    "products!inner(id, name)"
)


class OptimizeBody(BaseModel):
    list_id: str
    mode: str | None = None


@dataclass(frozen=True)
class OfferCandidate:
    offer_id: str
    product_id: str
    product_name: str
    supermarket_id: str
    supermarket_name: str
    price_offer: float
    price_original: float | None
    unit_price_value: float | None
    unit_price_unit: str | None
    unit_price_label: str | None
    valid_to: str | None


def _normalized(value: str) -> str:
    return " ".join(value.lower().split())


def _similarity(left: str, right: str) -> float:
    left_norm = _normalized(left)
    right_norm = _normalized(right)
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _load_list(sb: Any, list_id: str, user_id: str) -> dict:
    lists_repo.verify_member(sb, list_id, user_id)
    response = (
        sb.table("shopping_lists")
        .select("id, items")
        .eq("id", list_id)
        .maybe_single()
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    return response.data


def _load_active_offers(
    sb: Any,
    visible_supermarket_ids: set[str] | None,
) -> list[OfferCandidate]:
    query = apply_current_offer_window(
        sb.table("offers")
        .select(_OFFER_SELECT)
        .eq("is_confirmed", True)
        .eq("offer_kind", _PUBLIC_OFFER_KIND)
    )
    if visible_supermarket_ids is not None:
        if not visible_supermarket_ids:
            return []
        query = query.in_("supermarket_id", sorted(visible_supermarket_ids))
    response = query.execute()
    return [_to_offer_candidate(row) for row in response.data or []]


def _to_offer_candidate(row: dict) -> OfferCandidate:
    product = row.get("products") or {}
    return OfferCandidate(
        offer_id=row["id"],
        product_id=product["id"],
        product_name=product["name"],
        supermarket_id=row["supermarket_id"],
        supermarket_name=row.get("supermarket_name") or "",
        price_offer=float(row["price_offer"]),
        price_original=(
            float(row["price_original"]) if row.get("price_original") is not None else None
        ),
        unit_price_value=(
            float(row["unit_price_value"]) if row.get("unit_price_value") is not None else None
        ),
        unit_price_unit=row.get("unit_price_unit"),
        unit_price_label=row.get("unit_price")
        or format_unit_price_label(row.get("unit_price_value"), row.get("unit_price_unit")),
        valid_to=row.get("valid_to"),
    )


def _group_offers(offers: list[OfferCandidate]) -> tuple[dict[str, OfferCandidate], dict[str, list[OfferCandidate]]]:
    by_offer = {offer.offer_id: offer for offer in offers}
    by_product: dict[str, list[OfferCandidate]] = {}
    for offer in offers:
        by_product.setdefault(offer.product_id, []).append(offer)
    for product_offers in by_product.values():
        product_offers.sort(key=lambda offer: (offer.price_offer, offer.supermarket_name))
    return by_offer, by_product


def _alternatives(offers: list[OfferCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "offer_id": offer.offer_id,
            "product_id": offer.product_id,
            "product_name": offer.product_name,
            "supermarket_id": offer.supermarket_id,
            "supermarket_name": offer.supermarket_name,
            "price_offer": offer.price_offer,
            "price_original": offer.price_original,
            "unit_price_value": offer.unit_price_value,
            "unit_price_unit": offer.unit_price_unit,
            "unit_price_label": offer.unit_price_label,
            "valid_to": offer.valid_to,
        }
        for offer in offers
    ]


def _matched_product(item: dict, offer: OfferCandidate, product_offers: list[OfferCandidate]) -> dict[str, Any]:
    return {
        "item_id": item.get("id"),
        "source": "offer",
        "product_name": offer.product_name,
        "product_id": offer.product_id,
        "offer_id": offer.offer_id,
        "price_offer": offer.price_offer,
        "price_original": offer.price_original,
        "unit_price_value": offer.unit_price_value,
        "unit_price_unit": offer.unit_price_unit,
        "unit_price_label": offer.unit_price_label,
        "quantity": item.get("quantity", 1),
        "match_score": 1.0,
        "alternatives": _alternatives(product_offers),
    }


def _manual_product(item: dict, alternatives: list[OfferCandidate]) -> dict[str, Any]:
    return {
        "item_id": item.get("id"),
        "source": "manual",
        "product_name": item.get("name", ""),
        "product_id": None,
        "offer_id": None,
        "price_offer": None,
        "price_original": None,
        "unit_price_value": None,
        "unit_price_unit": None,
        "unit_price_label": None,
        "quantity": item.get("quantity", 1),
        "match_score": 0.0,
        "alternatives": _alternatives(alternatives),
    }


def _append_store_group(groups: dict[str, dict[str, Any]], product: dict[str, Any], offer: OfferCandidate) -> None:
    group = groups.setdefault(
        offer.supermarket_id,
        {
            "supermarket_id": offer.supermarket_id,
            "supermarket_name": offer.supermarket_name,
            "products": [],
        },
    )
    group["products"].append(product)


def _append_manual_group(groups: dict[str, dict[str, Any]], product: dict[str, Any]) -> None:
    group = groups.setdefault(
        "__manual__",
        {
            "supermarket_id": "__manual__",
            "supermarket_name": "Senza offerta",
            "products": [],
        },
    )
    group["products"].append(product)


def _best_fuzzy_matches(item_name: str, offers: list[OfferCandidate], limit: int = 5) -> list[OfferCandidate]:
    scored = [
        (offer, _similarity(item_name, offer.product_name))
        for offer in offers
    ]
    scored = [entry for entry in scored if entry[1] >= 0.6]
    scored.sort(key=lambda entry: (-entry[1], entry[0].price_offer))
    return [offer for offer, _score in scored[:limit]]


def _resolve_item(
    item: dict,
    by_offer_id: dict[str, OfferCandidate],
    by_product_id: dict[str, list[OfferCandidate]],
    offers: list[OfferCandidate],
) -> tuple[dict[str, Any], OfferCandidate | None]:
    pinned_offer_id = item.get("pinned_offer_id")
    if pinned_offer_id and pinned_offer_id in by_offer_id:
        offer = by_offer_id[pinned_offer_id]
        return _matched_product(item, offer, by_product_id.get(offer.product_id, [offer])), offer

    pinned_product_id = item.get("pinned_product_id")
    if pinned_product_id and pinned_product_id in by_product_id:
        offer = by_product_id[pinned_product_id][0]
        return _matched_product(item, offer, by_product_id[pinned_product_id]), offer

    alternatives = _best_fuzzy_matches(item.get("name", ""), offers)
    return _manual_product(item, alternatives), None


def _totals(store_groups: list[dict[str, Any]]) -> tuple[float, float]:
    total_cost = 0.0
    total_savings = 0.0
    for group in store_groups:
        for product in group["products"]:
            if product["price_offer"] is None:
                continue
            quantity = product.get("quantity", 1)
            total_cost += product["price_offer"] * quantity
            if product["price_original"] is not None:
                total_savings += max(product["price_original"] - product["price_offer"], 0) * quantity
    return round(total_cost, 2), round(total_savings, 2)


@router.post("")
async def optimize(
    body: OptimizeBody,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    sb = get_supabase()
    shopping_list = _load_list(sb, body.list_id, user_id)
    items = shopping_list.get("items") or []
    if not items:
        return {
            "store_groups": [],
            "total_savings": 0,
            "total_cost": 0,
            "unmatched_items": [],
            "coverage_percent": 100,
        }

    visible_supermarket_ids = visible_supermarket_ids_for_user(sb, user_id)
    offers = _load_active_offers(sb, visible_supermarket_ids)
    by_offer_id, by_product_id = _group_offers(offers)
    grouped: dict[str, dict[str, Any]] = {}

    for item in items:
        product, matched_offer = _resolve_item(item, by_offer_id, by_product_id, offers)
        if matched_offer is None:
            _append_manual_group(grouped, product)
            continue
        _append_store_group(grouped, product, matched_offer)

    store_groups = list(grouped.values())
    total_cost, total_savings = _totals(store_groups)
    return {
        "store_groups": store_groups,
        "total_savings": total_savings,
        "total_cost": total_cost,
        "unmatched_items": [],
        "coverage_percent": 100,
    }
