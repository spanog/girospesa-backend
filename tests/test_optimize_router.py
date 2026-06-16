from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.routers import optimize


def test_load_active_offers_cached_reuses_snapshot_within_ttl(monkeypatch):
    offers = [
        optimize.OfferCandidate(
            offer_id="offer-1",
            product_id="prod-1",
            product_name="Latte intero",
            supermarket_id="sm-1",
            supermarket_name="Coop",
            price_offer=1.29,
            price_original=1.59,
            unit_price_value=1.29,
            unit_price_unit="l",
            unit_price_label="1.29 €/l",
            valid_to="2099-12-31",
        )
    ]
    loader = MagicMock(return_value=offers)
    monkeypatch.setattr(optimize, "_load_active_offers", loader)
    monkeypatch.setattr(
        optimize,
        "_offer_snapshot_signature",
        MagicMock(return_value=optimize.OfferSnapshotSignature("1", "now", "2099-12-31", "0", "1.29")),
    )
    optimize._offers_cache.clear()

    visible_supermarket_ids = {"sm-1"}
    first = optimize._load_active_offers_cached(MagicMock(), visible_supermarket_ids)
    second = optimize._load_active_offers_cached(MagicMock(), visible_supermarket_ids)

    assert first == offers
    assert second == offers
    loader.assert_called_once()


def test_load_active_offers_cached_invalidates_when_signature_changes(monkeypatch):
    first_offers = [
        optimize.OfferCandidate(
            offer_id="offer-1",
            product_id="prod-1",
            product_name="Latte intero",
            supermarket_id="sm-1",
            supermarket_name="Coop",
            price_offer=1.29,
            price_original=1.59,
            unit_price_value=1.29,
            unit_price_unit="l",
            unit_price_label="1.29 €/l",
            valid_to="2099-12-31",
        )
    ]
    second_offers = [
        optimize.OfferCandidate(
            offer_id="offer-2",
            product_id="prod-2",
            product_name="Latte fresco",
            supermarket_id="sm-1",
            supermarket_name="Coop",
            price_offer=1.19,
            price_original=1.49,
            unit_price_value=1.19,
            unit_price_unit="l",
            unit_price_label="1.19 €/l",
            valid_to=None,
        )
    ]
    loader = MagicMock(side_effect=[first_offers, second_offers])
    signature = MagicMock(
        side_effect=[
            optimize.OfferSnapshotSignature("1", "a", "2099-12-31", "0", "1.29"),
            optimize.OfferSnapshotSignature("1", "b", "", "1", "1.19"),
        ]
    )
    monkeypatch.setattr(optimize, "_load_active_offers", loader)
    monkeypatch.setattr(optimize, "_offer_snapshot_signature", signature)
    optimize._offers_cache.clear()

    visible_supermarket_ids = {"sm-1"}
    first = optimize._load_active_offers_cached(MagicMock(), visible_supermarket_ids)
    second = optimize._load_active_offers_cached(MagicMock(), visible_supermarket_ids)

    assert first == first_offers
    assert second == second_offers
    assert loader.call_count == 2


def test_resolve_item_uses_catalog_shortlist_for_manual_alternatives():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(
        data=[{"id": "prod-1"}, {"id": "prod-2"}]
    )
    by_offer_id: dict[str, optimize.OfferCandidate] = {}
    by_product_id = {
        "prod-1": [
            optimize.OfferCandidate(
                offer_id="offer-1",
                product_id="prod-1",
                product_name="Latte intero",
                supermarket_id="sm-1",
                supermarket_name="Coop",
                price_offer=1.29,
                price_original=1.59,
                unit_price_value=1.29,
                unit_price_unit="l",
                unit_price_label="1.29 €/l",
                valid_to="2099-12-31",
            )
        ],
        "prod-2": [
            optimize.OfferCandidate(
                offer_id="offer-2",
                product_id="prod-2",
                product_name="Latte scremato",
                supermarket_id="sm-2",
                supermarket_name="Esselunga",
                price_offer=1.19,
                price_original=1.49,
                unit_price_value=1.19,
                unit_price_unit="l",
                unit_price_label="1.19 €/l",
                valid_to="2099-12-31",
            )
        ],
    }

    product, matched_offer = optimize._resolve_item(
        sb,
        {"id": "item-1", "name": "latte", "quantity": 1},
        by_offer_id,
        by_product_id,
        {},
    )

    assert matched_offer is None
    assert product["source"] == "manual"
    assert [alt["offer_id"] for alt in product["alternatives"]] == ["offer-1", "offer-2"]
    sb.rpc.assert_called_once_with(
        "search_products_catalog",
        {"query": "latte", "lim": optimize._SEARCH_SHORTLIST_LIMIT},
    )
