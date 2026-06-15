from services.list_offer_visibility import (
    HIDDEN_FOR_VIEWER,
    project_items_for_viewer,
    project_items_without_offers,
)


def test_project_items_without_offers_masks_stale_snapshot():
    items = [{
        "id": "item-1",
        "source": "offer",
        "name": "Latte",
        "pinned_offer_id": "offer-1",
        "pinned_product_id": "product-1",
        "found_deals": [{"offer_id": "offer-1", "supermarket_name": "Conad"}],
    }]

    projected = project_items_without_offers(items, {"offer-1"})

    assert projected[0]["source"] == "manual"
    assert projected[0]["pinned_offer_id"] is None
    assert projected[0]["found_deals"] == []
    assert projected[0]["offer_visibility_status"] is None


def test_hidden_offer_mask_wins_without_touching_persisted_snapshot_shape():
    items = [{
        "id": "item-1",
        "source": "offer",
        "name": "Latte",
        "pinned_offer_id": "offer-1",
        "pinned_product_id": "product-1",
        "found_deals": [{"offer_id": "offer-1", "supermarket_name": "Conad"}],
    }]

    projected = project_items_for_viewer(items, {"offer-1"})

    assert projected[0]["source"] == "manual"
    assert projected[0]["pinned_offer_id"] is None
    assert projected[0]["found_deals"] == []
    assert projected[0]["offer_visibility_status"] == HIDDEN_FOR_VIEWER
