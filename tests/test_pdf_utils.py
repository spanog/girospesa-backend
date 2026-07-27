from __future__ import annotations

import fitz

from services.extraction.pdf_utils import count_pdf_pages, iter_pdf_chunks, split_pdf_into_chunks


def _make_pdf_bytes(pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_split_pdf_into_chunks_uses_fixed_groups_of_three_pages() -> None:
    pdf_bytes = _make_pdf_bytes(7)

    chunks = split_pdf_into_chunks(pdf_bytes, 3)

    assert [(chunk.start_page, chunk.end_page) for chunk in chunks] == [
        (1, 3),
        (4, 6),
        (7, 7),
    ]
    assert [count_pdf_pages(chunk.pdf_bytes) for chunk in chunks] == [3, 3, 1]


def test_iter_pdf_chunks_yields_fixed_groups_without_materializing_list() -> None:
    pdf_bytes = _make_pdf_bytes(4)

    chunks = iter_pdf_chunks(pdf_bytes, 3)
    first = next(chunks)
    second = next(chunks)

    assert (first.start_page, first.end_page) == (1, 3)
    assert (second.start_page, second.end_page) == (4, 4)
