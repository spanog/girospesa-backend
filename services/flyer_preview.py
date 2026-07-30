"""Create compact cover previews for private flyer files."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps


PREVIEW_SIZE = (720, 960)
PREVIEW_QUALITY = 82
PDF_RENDER_SCALE = 1.2


def render_flyer_preview(content: bytes, content_type: str) -> bytes | None:
    try:
        image = _pdf_first_page(content) if content_type == "application/pdf" else _image(content)
    except (Image.DecompressionBombError, OSError, RuntimeError, ValueError):
        return None
    if image is None:
        return None
    try:
        return _webp(image)
    finally:
        image.close()


def _pdf_first_page(content: bytes) -> Image.Image | None:
    import fitz

    document = fitz.open(stream=content, filetype="pdf")
    try:
        if not document:
            return None
        pixmap = document.load_page(0).get_pixmap(
            matrix=fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE), alpha=False
        )
        with BytesIO(pixmap.tobytes("png")) as buffer:
            with Image.open(buffer) as source:
                return source.convert("RGB")
    finally:
        document.close()


def _image(content: bytes) -> Image.Image:
    with BytesIO(content) as buffer:
        with Image.open(buffer) as source:
            return ImageOps.exif_transpose(source).convert("RGB")


def _webp(image: Image.Image) -> bytes:
    preview = image.copy()
    try:
        preview.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)
        output = BytesIO()
        preview.save(output, format="WEBP", quality=PREVIEW_QUALITY, method=6)
        return output.getvalue()
    finally:
        preview.close()
