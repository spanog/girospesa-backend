from __future__ import annotations

from io import BytesIO

import fitz
from PIL import Image

from services.flyer_preview import PREVIEW_SIZE, render_flyer_preview


def _image_bytes() -> bytes:
    image = Image.new("RGB", (2_000, 1_000), "green")
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def _pdf_bytes() -> bytes:
    document = fitz.open()
    document.new_page(width=1_200, height=1_600).insert_text((72, 72), "Girospesa")
    content = document.tobytes()
    document.close()
    return content


def test_renders_image_file_to_bounded_webp() -> None:
    preview = render_flyer_preview(_image_bytes(), "image/png")

    assert preview is not None
    with Image.open(BytesIO(preview)) as image:
        assert image.format == "WEBP"
        assert image.width <= PREVIEW_SIZE[0]
        assert image.height <= PREVIEW_SIZE[1]


def test_renders_first_pdf_page_to_webp() -> None:
    preview = render_flyer_preview(_pdf_bytes(), "application/pdf")

    assert preview is not None
    with Image.open(BytesIO(preview)) as image:
        assert image.format == "WEBP"
        assert image.height > image.width


def test_returns_none_for_unrenderable_file() -> None:
    assert render_flyer_preview(b"not-a-pdf", "application/pdf") is None
