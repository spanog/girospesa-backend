"""
pdf_utils.py — PDF utility helpers (page count, chunking, page rasterization, file-type detection, MIME type).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple


class PdfChunk(NamedTuple):
    start_page: int
    end_page: int
    pdf_bytes: bytes


def _open_pdf(pdf_bytes: bytes):
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImportError(
            "PyMuPDF is required. Install with: pip install pymupdf"
        ) from exc

    return fitz.open(stream=pdf_bytes, filetype="pdf")


def is_pdf(filename: str) -> bool:
    return filename.lower().endswith(".pdf")


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Return the number of pages in a PDF without rendering them."""
    doc = _open_pdf(pdf_bytes)
    n = len(doc)
    doc.close()
    return n


def split_pdf_into_chunks(pdf_bytes: bytes, chunk_size: int) -> list[PdfChunk]:
    """Split a PDF into smaller PDF byte payloads with fixed page counts."""
    return list(iter_pdf_chunks(pdf_bytes, chunk_size))


def iter_pdf_chunks(pdf_bytes: bytes, chunk_size: int) -> Iterator[PdfChunk]:
    """Yield PDF chunks one at a time, keeping one generated chunk in memory."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    doc = _open_pdf(pdf_bytes)
    try:
        import fitz  # PyMuPDF

        total_pages = len(doc)
        for start_index in range(0, total_pages, chunk_size):
            end_index = min(start_index + chunk_size - 1, total_pages - 1)
            out_doc = fitz.open()
            out_doc.insert_pdf(doc, from_page=start_index, to_page=end_index)
            chunk_bytes = out_doc.tobytes()
            out_doc.close()
            yield PdfChunk(
                start_page=start_index + 1,
                end_page=end_index + 1,
                pdf_bytes=chunk_bytes,
            )
    finally:
        doc.close()


def split_pdf_to_jpeg_pages(pdf_bytes: bytes, dpi: int = 150) -> list[bytes]:
    """Render each PDF page to JPEG bytes."""
    doc = _open_pdf(pdf_bytes)
    scale = dpi / 72
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        doc.close()
        raise ImportError(
            "PyMuPDF is required. Install with: pip install pymupdf"
        ) from exc

    matrix = fitz.Matrix(scale, scale)
    pages: list[bytes] = []
    for page_index in range(len(doc)):
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=matrix)  # type: ignore[attr-defined]
        pages.append(pix.tobytes("jpeg"))
    doc.close()
    return pages


def mime_type_for_filename(filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        return "application/pdf"
    ext = filename.rsplit(".", 1)[-1].lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
