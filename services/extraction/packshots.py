"""Render and crop AI-localized product packshots from flyer PDF pages."""
from __future__ import annotations

from io import BytesIO
from typing import Sequence

from PIL import Image


_BOX_GRID_SIZE = 1000


def normalized_box(value: object) -> tuple[int, int, int, int] | None:
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 4:
        return None
    try:
        y1, x1, y2, x2 = (int(float(item)) for item in value)
    except (TypeError, ValueError):
        return None
    if not 0 <= y1 < y2 <= _BOX_GRID_SIZE or not 0 <= x1 < x2 <= _BOX_GRID_SIZE:
        return None
    return y1, x1, y2, x2


def render_packshot(pdf_bytes: bytes, page_number: int, box: object) -> bytes | None:
    coordinates = normalized_box(box)
    if coordinates is None or page_number < 1:
        return None
    image = _render_page(pdf_bytes, page_number)
    if image is None:
        return None
    crop = image.crop(_pixel_box(image.size, expanded_box(coordinates)))
    try:
        return _png_bytes(crop)
    finally:
        crop.close()
        image.close()


def _render_page(pdf_bytes: bytes, page_number: int) -> Image.Image | None:
    try:
        import fitz

        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        if page_number > len(document):
            document.close()
            return None
        pixmap = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
        document.close()
        with BytesIO(pixmap.tobytes("png")) as buffer:
            with Image.open(buffer) as source:
                return source.convert("RGB")
    except (ImportError, RuntimeError, ValueError):
        return None


def _pixel_box(size: tuple[int, int], box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    width, height = size
    y1, x1, y2, x2 = box
    return x1 * width // _BOX_GRID_SIZE, y1 * height // _BOX_GRID_SIZE, x2 * width // _BOX_GRID_SIZE, y2 * height // _BOX_GRID_SIZE


def expanded_box(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Add safe context so visual detection does not sever a packshot edge."""
    y1, x1, y2, x2 = box
    height, width = y2 - y1, x2 - x1
    portrait = height > width * 1.4
    horizontal = max(35, round(width * (0.45 if portrait else 0.18)))
    vertical = max(20, round(height * 0.10))
    return max(0, y1 - vertical), max(0, x1 - horizontal), min(1000, y2 + vertical), min(1000, x2 + horizontal)


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
