from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.extraction.normalizer import normalize_product
from services.extraction.packshots import expanded_box, normalized_box


def test_normalized_box_accepts_gemma_grid_coordinates() -> None:
    assert normalized_box([100, "200", 900.0, 800]) == (100, 200, 900, 800)


def test_normalized_box_rejects_invalid_coordinates() -> None:
    assert normalized_box([0, 10, 1001, 20]) is None
    assert normalized_box([100, 100, 50, 200]) is None


def test_expanded_box_adds_extra_horizontal_margin_to_portrait_packshots() -> None:
    assert expanded_box((100, 400, 800, 500)) == (30, 355, 870, 545)


def test_normalizer_keeps_valid_packshot_metadata() -> None:
    product = normalize_product({
        "name": "Passata", "price_current": "1,89",
        "format": {"tipo": "confezione_singola"}, "source_page": "2",
        "packshot_bbox": [100, 200, 900, 800],
    })

    assert product["source_page"] == 2
    assert product["packshot_bbox"] == [100, 200, 900, 800]


def test_normalizer_discards_invalid_packshot_metadata() -> None:
    product = normalize_product({
        "name": "Passata", "price_current": 1.89,
        "format": {"tipo": "confezione_singola"}, "source_page": "no",
        "packshot_bbox": [100, 200, 50, 800],
    })

    assert product["source_page"] is None
    assert product["packshot_bbox"] is None


def test_packshot_upload_populates_empty_draft_image() -> None:
    from services.extraction.service import ExtractionService

    sb = MagicMock()
    offer_query = sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.maybe_single.return_value
    offer_query.execute.return_value.data = {"id": "offer-1", "image_url": None}
    sb.storage.from_.return_value.get_public_url.return_value = "https://storage.test/packshot.png"
    row = {"flyer_id": "flyer-1", "offer_key": "passata|petti", "format_key": "v1:{}"}
    product = {"source_page": 1, "packshot_bbox": [100, 200, 900, 800]}

    with patch("services.extraction.service.render_packshot", return_value=b"png"):
        ExtractionService()._save_packshot(sb, row, b"%PDF", product)

    sb.storage.from_.return_value.upload.assert_called_once()
    sb.table.return_value.update.assert_called_once_with({"image_url": "https://storage.test/packshot.png"})


def test_packshot_upload_preserves_manual_draft_image() -> None:
    from services.extraction.service import ExtractionService

    sb = MagicMock()
    offer_query = sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.maybe_single.return_value
    offer_query.execute.return_value.data = {"id": "offer-1", "image_url": "https://manual.test/image.png"}
    row = {"flyer_id": "flyer-1", "offer_key": "passata|petti", "format_key": "v1:{}"}
    product = {"source_page": 1, "packshot_bbox": [100, 200, 900, 800]}

    with patch("services.extraction.service.render_packshot") as render:
        ExtractionService()._save_packshot(sb, row, b"%PDF", product)

    render.assert_not_called()
    sb.storage.from_.assert_not_called()
