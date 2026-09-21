from __future__ import annotations

from io import BytesIO

import fitz
from PIL import Image

from services.flyer_preview import PREVIEW_QUALITY, PREVIEW_SIZE, _webp, render_flyer_preview


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


def test_pdf_preview_keeps_legacy_webp_pixels_without_png_buffer() -> None:
    content = _pdf_bytes()
    document = fitz.open(stream=content, filetype="pdf")
    pixmap = document.load_page(0).get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
    with BytesIO(pixmap.tobytes("png")) as buffer:
        with Image.open(buffer) as legacy_source:
            expected = _webp(legacy_source.convert("RGB"))
    document.close()

    preview = render_flyer_preview(content, "application/pdf")

    assert preview == expected
    with Image.open(BytesIO(preview)) as image:
        assert image.format == "WEBP"
        assert PREVIEW_QUALITY == 82


def test_returns_none_for_unrenderable_file() -> None:
    assert render_flyer_preview(b"not-a-pdf", "application/pdf") is None
