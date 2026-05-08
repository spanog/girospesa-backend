from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class PdfChunkExtractionError(Exception):
    chunk_index: int
    chunks_total: int
    start_page: int
    end_page: int
    retry_errors: list[str]

    def __str__(self) -> str:
        return (
            f"Chunk {self.chunk_index}/{self.chunks_total} "
            f"(pages {self.start_page}-{self.end_page}) failed after {len(self.retry_errors)} attempts"
        )


class ExtractionProvider(Protocol):
    def extract_products(
        self,
        file_bytes: bytes,
        mime_type: str,
        progress_callback: Callable[[dict], None] | None = None,
        chunk_result_callback: Callable[[dict], None] | None = None,
        start_chunk_index: int = 1,
    ) -> tuple[list[dict], list[str]]:
        """Return (products, retry_errors). Products may be empty on total failure."""
        ...
