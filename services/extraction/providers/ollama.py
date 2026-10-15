"""Page-wise local vision extraction, preserving PDF checkpoint contracts."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from services.extraction.pdf_utils import PdfSource, count_pdf_pages, iter_pdf_chunks, split_pdf_to_jpeg_pages
from services.extraction.providers.base import PdfChunkExtractionError
from services.extraction.providers.ollama_client import OllamaClient, OllamaExtractionError

Callback = Callable[[dict], None]
MAX_ATTEMPTS = 2
SUPPORTED_IMAGES = frozenset({"image/jpeg", "image/png", "image/webp"})


@dataclass(frozen=True)
class ExtractionChunk:
    index: int
    total: int
    start: int
    end: int

    def payload(self) -> dict:
        return {"chunk_index": self.index, "chunks_total": self.total,
                "current_chunk_start": self.start, "current_chunk_end": self.end}

    def progress(self, completed: bool, found: int) -> dict:
        return {**self.payload(), "chunks_completed": self.index if completed else self.index - 1,
                "pages_processed": self.end if completed else self.start - 1, "products_found": found}


class OllamaProvider:
    def __init__(self, client: OllamaClient, image_dpi: int = 150, chunk_size_pages: int = 1) -> None:
        self._client = client
        self._image_dpi = image_dpi
        self.chunk_size_pages = chunk_size_pages

    def extract_products(
        self, file_bytes: bytes | Path, mime_type: str,
        progress_callback: Callback | None = None, chunk_result_callback: Callback | None = None,
        start_chunk_index: int = 1,
    ) -> tuple[list[dict], list[str]]:
        if mime_type == "application/pdf":
            return self._extract_pdf(file_bytes, progress_callback, chunk_result_callback, start_chunk_index)
        if mime_type not in SUPPORTED_IMAGES or not isinstance(file_bytes, bytes):
            raise ValueError("Ollama extraction requires PDF, JPEG, PNG or WebP input")
        return self._extract_image(file_bytes, page=1)

    def _extract_image(self, image: bytes, page: int) -> tuple[list[dict], list[str]]:
        errors: list[str] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                products = self._client.extract(image)
                return [{**product, "source_page": page} for product in products], errors
            except OllamaExtractionError as exc:
                errors.append(f"Page {page}, attempt {attempt}/{MAX_ATTEMPTS}: {exc}")
        return [], errors

    def _extract_pdf(
        self, source: PdfSource, progress: Callback | None, persist: Callback | None, start: int,
    ) -> tuple[list[dict], list[str]]:
        products: list[dict] = []
        errors: list[str] = []
        found = 0
        for chunk, pdf in self._pending_chunks(source, start):
            if progress:
                progress(chunk.progress(False, found))
            extracted, retries = self._extract_chunk(pdf, chunk)
            self._persist_chunk(persist, chunk, extracted, retries, products)
            errors.extend(retries)
            found += len(extracted)
            if progress:
                progress(chunk.progress(True, found))
        return products, errors

    def _pending_chunks(self, source: PdfSource, start: int) -> Iterator[tuple[ExtractionChunk, bytes]]:
        total = (count_pdf_pages(source) + self.chunk_size_pages - 1) // self.chunk_size_pages
        if start < 1 or start > total:
            raise ValueError(f"Invalid start_chunk_index {start} for {total} chunk(s)")
        for index, pdf in enumerate(iter_pdf_chunks(source, self.chunk_size_pages), start=1):
            if index >= start:
                yield ExtractionChunk(index, total, pdf.start_page, pdf.end_page), pdf.pdf_bytes

    def _extract_chunk(self, pdf: bytes, chunk: ExtractionChunk) -> tuple[list[dict], list[str]]:
        images = split_pdf_to_jpeg_pages(pdf, dpi=self._image_dpi)
        errors: list[str] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = self._client.extract_pages(images)
                return _absolute_pages(raw, chunk), errors
            except OllamaExtractionError as exc:
                errors.append(f"Chunk {chunk.index}, attempt {attempt}/{MAX_ATTEMPTS}: {exc}")
        raise PdfChunkExtractionError(chunk.index, chunk.total, chunk.start, chunk.end, errors)

    @staticmethod
    def _persist_chunk(
        persist: Callback | None, chunk: ExtractionChunk, extracted: list[dict],
        retries: list[str], products: list[dict],
    ) -> None:
        if persist:
            persist({**chunk.payload(), "products": extracted, "retry_errors": retries})
        else:
            products.extend(extracted)


def _absolute_pages(products: list[dict], chunk: ExtractionChunk) -> list[dict]:
    result: list[dict] = []
    count = chunk.end - chunk.start + 1
    for product in products:
        page = product.get("source_page") if count > 1 else 1
        if type(page) is not int or not 1 <= page <= count:
            raise OllamaExtractionError(f"Invalid source_page {page!r} for {count}-page batch")
        result.append({**product, "source_page": chunk.start + page - 1})
    return result
