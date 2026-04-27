from __future__ import annotations

from typing import Protocol


class ExtractionProvider(Protocol):
    def extract_products(
        self, file_bytes: bytes, mime_type: str
    ) -> tuple[list[dict], list[str]]:
        """Return (products, retry_errors). Products may be empty on total failure."""
        ...
