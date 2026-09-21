"""Render and crop AI-localized product packshots from flyer PDF pages."""
from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from typing import Mapping, Sequence

from PIL import Image

from services.extraction.pdf_utils import PdfSource

_BOX_GRID_SIZE = 1000
_PACKSHOT_RENDER_SCALE = 2
_PACKSHOT_MAX_SIDE = 640
_PACKSHOT_WEBP_QUALITY = 82


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


def render_packshot(pdf_source: PdfSource, page_number: int, box: object) -> bytes | None:
    return render_page_packshots(pdf_source, page_number, {"packshot": box}).get("packshot")


def render_page_packshots(
    pdf_source: PdfSource, page_number: int, boxes: Mapping[str, object]
) -> dict[str, bytes]:
    return dict(iter_page_packshots(pdf_source, page_number, boxes))


def iter_page_packshots(
    pdf_source: PdfSource, page_number: int, boxes: Mapping[str, object]
) -> Iterator[tuple[str, bytes]]:
    """Yield each crop before rendering the next one to bound peak memory."""
    image = _render_page(pdf_source, page_number)
    if image is None:
        return
    try:
        yield from _iter_crops(image, boxes)
    finally:
        image.close()


def _iter_crops(image: Image.Image, boxes: Mapping[str, object]) -> Iterator[tuple[str, bytes]]:
    for key, box in boxes.items():
        coordinates = normalized_box(box)
        if coordinates is None:
            continue
        crop = image.crop(_pixel_box(image.size, expanded_box(coordinates)))
        try:
            yield key, _webp_bytes(crop)
        finally:
            crop.close()


def _render_page(pdf_source: PdfSource, page_number: int) -> Image.Image | None:
    document = None
    try:
        import fitz

        document = (
            fitz.open(stream=pdf_source, filetype="pdf")
            if isinstance(pdf_source, bytes)
            else fitz.open(filename=str(pdf_source))
        )
        if page_number > len(document):
            return None
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(_PACKSHOT_RENDER_SCALE, _PACKSHOT_RENDER_SCALE), alpha=False
        )
        try:
            return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            del pixmap
            del page
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
    finally:
        if document is not None:
            document.close()


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


def _webp_bytes(image: Image.Image) -> bytes:
    image.thumbnail((_PACKSHOT_MAX_SIDE, _PACKSHOT_MAX_SIDE), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="WEBP", quality=_PACKSHOT_WEBP_QUALITY, method=6)
    return output.getvalue()
