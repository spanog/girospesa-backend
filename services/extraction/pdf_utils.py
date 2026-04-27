"""
pdf_utils.py — PDF utility helpers (page count, file-type detection, MIME type).
"""

from __future__ import annotations


def is_pdf(filename: str) -> bool:
    return filename.lower().endswith(".pdf")


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Return the number of pages in a PDF without rendering them."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImportError(
            "PyMuPDF is required. Install with: pip install pymupdf"
        ) from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n = len(doc)
    doc.close()
    return n


def mime_type_for_filename(filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        return "application/pdf"
    ext = filename.rsplit(".", 1)[-1].lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
