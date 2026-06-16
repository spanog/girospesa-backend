"""Unit tests for services/deal_freshness.py — no DB, no network."""

from __future__ import annotations

import pytest

from services.deal_freshness import classify_deal_freshness, DealFreshnessStatus

_OFFER_ID = "offer-aaa"
_PRODUCT_ID = "product-bbb"
_ITEM_ID = "item-ccc"


def _item(
    pinned_offer_id: str | None = _OFFER_ID,
    pinned_product_id: str | None = _PRODUCT_ID,
    pinned_price: float | None = 1.29,
) -> dict:
    found_deals = []
    if pinned_offer_id and pinned_price is not None:
        found_deals = [{"offer_id": pinned_offer_id, "price_offer": pinned_price}]
    return {
        "id": _ITEM_ID,
        "name": "Latte intero",
        "pinned_offer_id": pinned_offer_id,
        "pinned_product_id": pinned_product_id,
        "found_deals": found_deals,
    }


def _offer(is_active: bool = True, price: float = 1.29, valid_to: str = "2099-12-31") -> dict:
    return {
        "id": _OFFER_ID,
        "price_offer": price,
        "valid_from": "2000-01-01",
        "valid_to": valid_to,
        "is_active": is_active,
    }


class TestClassifyDealFreshness:

    def test_fresh_offer_same_price(self):
        result = classify_deal_freshness([_item()], {_OFFER_ID: _offer()})
        assert len(result) == 1
        assert result[0]["status"] == DealFreshnessStatus.FRESH

    def test_fresh_offer_within_1_cent_tolerance(self):
        """Price difference ≤ 0.01 is not considered a change."""
        result = classify_deal_freshness([_item(pinned_price=1.29)], {_OFFER_ID: _offer(price=1.295)})
        assert result[0]["status"] == DealFreshnessStatus.FRESH

    def test_expired_offer(self):
        offer = _offer(is_active=False, valid_to="2000-01-01")
        result = classify_deal_freshness([_item()], {_OFFER_ID: offer})
        assert result[0]["status"] == DealFreshnessStatus.EXPIRED
        assert result[0]["valid_to"] == "2000-01-01"

    def test_expired_offer_when_valid_to_is_past_even_if_is_active_flag_is_stale(self):
        offer = _offer(is_active=True, valid_to="2000-01-01")
        result = classify_deal_freshness([_item()], {_OFFER_ID: offer})
        assert result[0]["status"] == DealFreshnessStatus.EXPIRED

    def test_future_offer_is_not_treated_as_fresh_even_if_is_active_flag_is_stale(self):
        offer = {
            "id": _OFFER_ID,
            "price_offer": 1.29,
            "valid_from": "2999-01-01",
            "valid_to": "2999-01-31",
            "is_active": True,
        }
        result = classify_deal_freshness([_item()], {_OFFER_ID: offer})
        assert result[0]["status"] == DealFreshnessStatus.EXPIRED

    def test_price_changed(self):
        result = classify_deal_freshness([_item(pinned_price=1.29)], {_OFFER_ID: _offer(price=1.49)})
        assert result[0]["status"] == DealFreshnessStatus.PRICE_CHANGED
        assert result[0]["current_price"] == pytest.approx(1.49)
        assert result[0]["pinned_price"] == pytest.approx(1.29)

    def test_unavailable_offer(self):
        result = classify_deal_freshness([_item()], {})
        assert result[0]["status"] == DealFreshnessStatus.UNAVAILABLE
        assert result[0]["current_price"] is None

    def test_item_without_pinned_offer_is_skipped(self):
        result = classify_deal_freshness([_item(pinned_offer_id=None)], {})
        assert result == []

    def test_empty_list(self):
        assert classify_deal_freshness([], {}) == []

    def test_multiple_items_mixed_statuses(self):
        offer_expired = "offer-expired"
        offer_fresh = "offer-fresh"
        items = [
            {
                "id": "i1", "name": "Pane", "pinned_offer_id": offer_expired,
                "pinned_product_id": "p1",
                "found_deals": [{"offer_id": offer_expired, "price_offer": 1.00}],
            },
            {
                "id": "i2", "name": "Latte", "pinned_offer_id": offer_fresh,
                "pinned_product_id": "p2",
                "found_deals": [{"offer_id": offer_fresh, "price_offer": 0.99}],
            },
            {
                "id": "i3", "name": "Manuale", "pinned_offer_id": None,
                "pinned_product_id": None, "found_deals": [],
            },
        ]
        offers = {
            offer_expired: {"id": offer_expired, "price_offer": 1.00, "valid_to": "2000-01-01", "is_active": False},
            offer_fresh: {"id": offer_fresh, "price_offer": 0.99, "valid_to": "2099-12-31", "is_active": True},
        }
        result = classify_deal_freshness(items, offers)
        assert len(result) == 2
        statuses = {r["list_item_name"]: r["status"] for r in result}
        assert statuses["Pane"] == DealFreshnessStatus.EXPIRED
        assert statuses["Latte"] == DealFreshnessStatus.FRESH
