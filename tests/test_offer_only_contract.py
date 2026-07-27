from __future__ import annotations

from types import SimpleNamespace

from api.routers._offer_utils import _flatten_draft_offer, build_offer_row


def test_offer_response_contains_no_catalog_binding() -> None:
    offer = _flatten_draft_offer(
        {
            "id": "offer-1",
            "name": "Passata di pomodoro",
            "brand": "Pomi",
            "image_url": "https://storage.test/offer-1.png",
            "price_offer": 1.29,
        }
    )

    assert offer["name"] == "Passata di pomodoro"
    assert offer["image_url"].endswith("offer-1.png")
    assert "product_id" not in offer
    assert "linked_product" not in offer
    assert "binding_status" not in offer


def test_offer_response_hides_internal_packshot_metadata() -> None:
    offer = _flatten_draft_offer(
        {
            "name": "Passata",
            "packshot_source_page": 3,
            "packshot_bbox": [100, 200, 900, 800],
        }
    )

    assert "packshot_source_page" not in offer
    assert "packshot_bbox" not in offer


def test_manual_draft_row_is_self_contained_offer() -> None:
    payload = SimpleNamespace(
        name="Passata di pomodoro",
        brand="Pomi",
        category="dispensa",
        subcategory="Conserve",
        price_offer=1.29,
        price_original=None,
        unit_price_value=None,
        offer_notes=None,
        valid_from=None,
        valid_to=None,
    )

    row = build_offer_row(payload, "super-1", "Conad", "flyer-1", None)

    assert row["offer_key"] == "passata di pomodoro|pomi"
    assert row["name"] == "Passata di pomodoro"
    assert "product_id" not in row
    assert "draft_name" not in row
