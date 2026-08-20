from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.extraction.normalizer import normalize_product
from PIL import Image

from services.extraction.packshots import expanded_box, normalized_box, render_page_packshots


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
    sb.storage.from_.return_value.get_public_url.return_value = "https://storage.test/packshot.png"

    ExtractionService()._upload_packshot(sb, {"id": "offer-1"}, b"png")

    sb.storage.from_.return_value.upload.assert_called_once()
    assert sb.storage.from_.return_value.upload.call_args.kwargs["file_options"] == {
        "content-type": "image/webp",
        "cache-control": "31536000",
        "upsert": "false",
    }
    sb.table.return_value.update.assert_called_once_with({"image_url": "https://storage.test/packshot.png"})


def test_pending_packshots_are_uploaded_from_persisted_metadata() -> None:
    from services.extraction.service import ExtractionService

    sb = MagicMock()
    pending = (
        sb.table.return_value.select.return_value.eq.return_value.is_.return_value.not_.is_.return_value.not_.is_.return_value
    )
    pending.execute.return_value.data = [
        {"id": "offer-1", "image_url": None, "packshot_source_page": 2, "packshot_bbox": [1, 2, 3, 4]}
    ]

    with patch("services.extraction.service.render_page_packshots", return_value={"offer-1": b"png"}) as render, patch.object(ExtractionService, "_upload_packshot") as upload:
        ExtractionService()._save_pending_packshots(sb, "flyer-1", b"%PDF")

    render.assert_called_once_with(b"%PDF", 2, {"offer-1": [1, 2, 3, 4]})
    upload.assert_called_once_with(sb, pending.execute.return_value.data[0], b"png")


def test_page_packshots_render_page_once_for_multiple_crops() -> None:
    page = Image.new("RGB", (1000, 1000))

    with patch("services.extraction.packshots._render_page", return_value=page) as render:
        crops = render_page_packshots(b"%PDF", 2, {"one": [100, 100, 300, 300], "two": [400, 400, 700, 700]})

    assert render.call_count == 1
    assert set(crops) == {"one", "two"}


def test_packshot_bytes_are_webp_and_bounded_to_640_pixels() -> None:
    page = Image.new("RGB", (2_000, 1_000), color="green")

    with patch("services.extraction.packshots._render_page", return_value=page):
        image_bytes = render_page_packshots(b"%PDF", 1, {"one": [0, 0, 1000, 1000]})["one"]

    with Image.open(__import__("io").BytesIO(image_bytes)) as rendered:
        assert rendered.format == "WEBP"
        assert max(rendered.size) == 640
