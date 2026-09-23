"""Create and validate ordered PDF files from retailer viewer images."""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


class FlyerPdfError(ValueError):
    """Raised when viewer images cannot produce a valid flyer PDF."""


def build_pdf_from_images(image_paths: tuple[Path, ...], output_path: Path) -> int:
    """Create a deterministic, page-ordered A4 PDF from verified image files."""
    if not image_paths:
        raise FlyerPdfError("At least one viewer image is required")
    verified = tuple(_verified_image(path) for path in image_paths)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(output_path), pagesize=A4, invariant=1)
    for image_path, image_size in zip(image_paths, verified, strict=True):
        _draw_page(canvas, image_path, image_size)
        canvas.showPage()
    canvas.save()
    validate_pdf(output_path, len(image_paths))
    return len(image_paths)


def validate_pdf(path: Path, expected_pages: int) -> None:
    """Ensure the generated file is a readable PDF with every expected page."""
    try:
        document = fitz.open(path)
    except (fitz.FileDataError, OSError, RuntimeError, ValueError) as exc:
        raise FlyerPdfError("Generated PDF cannot be opened") from exc
    try:
        if document.page_count != expected_pages:
            raise FlyerPdfError("Generated PDF has an unexpected page count")
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25))
            if not pixmap.samples:
                raise FlyerPdfError("Generated PDF contains an empty page")
    finally:
        document.close()


def _verified_image(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise FlyerPdfError(f"Viewer image is invalid: {path.name}") from exc
    if width < 1 or height < 1:
        raise FlyerPdfError(f"Viewer image has no pixels: {path.name}")
    return width, height


def _draw_page(canvas: Canvas, image_path: Path, image_size: tuple[int, int]) -> None:
    page_width, page_height = A4
    image_width, image_height = image_size
    scale = min(page_width / image_width, page_height / image_height)
    width = image_width * scale
    height = image_height * scale
    canvas.drawImage(
        ImageReader(str(image_path)),
        (page_width - width) / 2,
        (page_height - height) / 2,
        width=width,
        height=height,
        preserveAspectRatio=True,
        mask="auto",
    )
