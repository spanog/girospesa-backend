"""Deal freshness classification service.

Classifies each pinned offer in a shopping list as:
- fresh: offer is inside its validity window and price unchanged
- price_changed: offer is inside its validity window but price differs from the pinned snapshot
- expired: offer validity window does not include today
- unavailable: no offer row found for the pinned_offer_id

Used by GET /lists/{list_id}/deal-freshness.
"""

from __future__ import annotations

from enum import Enum
from typing import TypedDict

from services.offer_visibility import offer_is_current


class DealFreshnessStatus(str, Enum):
    FRESH = "fresh"
    PRICE_CHANGED = "price_changed"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class DealFreshnessItem(TypedDict):
    list_item_id: str
    list_item_name: str
    pinned_offer_id: str | None
    status: str  # DealFreshnessStatus value
    current_price: float | None
    pinned_price: float | None
    valid_to: str | None


def classify_deal_freshness(
    list_items: list[dict],
    offers_by_id: dict[str, dict],
) -> list[DealFreshnessItem]:
    """Classify the freshness of every item that has a pinned_offer_id.

    Args:
        list_items: raw JSONB items from shopping_lists.items
        offers_by_id: map of offer_id → offer row fetched from the DB
            Each row must have: id, price_offer, valid_from, valid_to

    Returns:
        One entry per list item that has a pinned_offer_id.
        Items without a pinned_offer_id are omitted.
    """
    results: list[DealFreshnessItem] = []

    for item in list_items:
        offer_id: str | None = item.get("pinned_offer_id")
        if not offer_id:
            continue

        pinned_price: float | None = _extract_pinned_price(item)
        offer = offers_by_id.get(offer_id)

        if offer is None:
            status = DealFreshnessStatus.UNAVAILABLE
            current_price = None
            valid_to = None
        elif not offer_is_current_now(offer):
            status = DealFreshnessStatus.EXPIRED
            current_price = offer.get("price_offer")
            valid_to = offer.get("valid_to")
        elif pinned_price is not None and _price_changed(pinned_price, offer["price_offer"]):
            status = DealFreshnessStatus.PRICE_CHANGED
            current_price = offer["price_offer"]
            valid_to = offer.get("valid_to")
        else:
            status = DealFreshnessStatus.FRESH
            current_price = offer["price_offer"]
            valid_to = offer.get("valid_to")

        results.append(
            DealFreshnessItem(
                list_item_id=item.get("id", ""),
                list_item_name=item.get("name", ""),
                pinned_offer_id=offer_id,
                status=status.value,
                current_price=current_price,
                pinned_price=pinned_price,
                valid_to=valid_to,
            )
        )

    return results


def _extract_pinned_price(item: dict) -> float | None:
    """Return the price stored in the item's found_deals snapshot, if any."""
    deals: list[dict] = item.get("found_deals") or []
    offer_id = item.get("pinned_offer_id")
    for deal in deals:
        if deal.get("offer_id") == offer_id:
            price = deal.get("price_offer")
            if price is not None:
                return float(price)
    return None


def _price_changed(pinned: float, current: float) -> bool:
    """Return True if prices differ by more than 1 cent."""
    return abs(pinned - current) > 0.01


def offer_is_current_now(offer: dict) -> bool:
    """Return whether the offer validity window includes the current Rome day."""
    return offer_is_current(offer)
