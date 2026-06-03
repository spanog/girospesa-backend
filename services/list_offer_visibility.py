from __future__ import annotations

from typing import Iterable

from services.location import load_nearby_distances

HIDDEN_FOR_VIEWER = "hidden_for_viewer"


def visible_supermarket_ids_for_user(sb, user_id: str) -> set[str] | None:
    distances = load_nearby_distances(sb, user_id)
    if distances is None:
        return None
    return set(distances.keys())


def hidden_offer_ids_for_viewer(
    items: list[dict],
    offer_rows: Iterable[dict],
    visible_supermarket_ids: set[str] | None,
) -> set[str]:
    if visible_supermarket_ids is None:
        return set()
    supermarket_by_offer = {
        row["id"]: row.get("supermarket_id")
        for row in offer_rows
    }
    hidden_ids: set[str] = set()
    for item in items:
        offer_id = item.get("pinned_offer_id")
        if not offer_id:
            continue
        supermarket_id = supermarket_by_offer.get(offer_id)
        if supermarket_id and supermarket_id not in visible_supermarket_ids:
            hidden_ids.add(offer_id)
    return hidden_ids


def project_item_for_viewer(item: dict, hidden_offer_ids: set[str]) -> dict:
    offer_id = item.get("pinned_offer_id")
    if not offer_id or offer_id not in hidden_offer_ids:
        return item
    return {
        **item,
        "source": "manual",
        "pinned_offer_id": None,
        "found_deals": [],
        "offer_visibility_status": HIDDEN_FOR_VIEWER,
    }


def project_items_for_viewer(items: list[dict], hidden_offer_ids: set[str]) -> list[dict]:
    if not hidden_offer_ids:
        return items
    return [project_item_for_viewer(item, hidden_offer_ids) for item in items]
