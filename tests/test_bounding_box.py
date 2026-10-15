"""Coordinate contracts: named model fields, canonical x/y, versioned legacy data."""
import pytest
from services.extraction.bounding_box import BoundingBox, BoundingBoxVersion, InvalidBoundingBox
from services.extraction.normalizer import normalize_product
from services.extraction.packshots import _pixel_box, expanded_box


def test_named_coordinates_are_canonical_xy():
    box = BoundingBox.parse({"x_min": 730, "y_min": 120, "x_max": 950, "y_max": 340})
    assert box.as_list() == [730, 120, 950, 340]
    assert _pixel_box((2000, 1000), box.as_tuple()) == (1460, 120, 1900, 340)


def test_legacy_and_new_storage_resolve_same_region():
    old = BoundingBox.parse([120, 730, 340, 950], BoundingBoxVersion.LEGACY_YX)
    new = BoundingBox.parse([730, 120, 950, 340], BoundingBoxVersion.XY)
    assert old == new
    assert expanded_box(old.as_tuple()) == expanded_box(new.as_tuple())


def test_normalizer_emits_named_box_as_xy_and_marks_version():
    product = normalize_product({"name": "Latte", "price_current": 1.29,
                                 "packshot_bbox": {"x_min": 730, "y_min": 120, "x_max": 950, "y_max": 340}})
    assert product["packshot_bbox"] == [730, 120, 950, 340]
    assert product["packshot_bbox_version"] == 2
    assert normalize_product(product)["packshot_bbox"] == product["packshot_bbox"]


@pytest.mark.parametrize("value", [{"x_min": 1}, [0, 0, float('nan'), 5],
                                   {"x_min": 950, "y_min": 120, "x_max": 730, "y_max": 340},
                                   [0, 0, 1001, 1000]])
def test_invalid_box_rejected(value):
    with pytest.raises(InvalidBoundingBox):
        BoundingBox.parse(value)


def test_versioned_storage_keeps_asymmetric_legacy_geometry():
    from services.extraction.bounding_box import stored_box
    assert stored_box({"packshot_bbox": [120, 730, 340, 950]}) == [730, 120, 950, 340]
    assert stored_box({"packshot_bbox": [730, 120, 950, 340], "packshot_bbox_version": 2}) == [730, 120, 950, 340]
    assert stored_box({"packshot_bbox": [730, 120, 950, 340], "packshot_bbox_version": 9}) is None


def test_xy_crop_targets_top_right_product_on_non_square_page():
    from io import BytesIO
    from PIL import Image, ImageDraw
    from unittest.mock import patch
    from services.extraction.packshots import render_page_packshots
    page = Image.new("RGB", (2000, 1000), "red")
    ImageDraw.Draw(page).rectangle((1400, 100, 1960, 400), fill="green")
    with patch("services.extraction.packshots._render_page", return_value=page):
        content = render_page_packshots(b"pdf", 1, {"product": [730, 120, 950, 340]})["product"]
    with Image.open(BytesIO(content)) as crop:
        red, green, blue = crop.getpixel((crop.width // 2, crop.height // 2))
    assert green > red * 2 and green > blue * 2
