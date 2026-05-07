from __future__ import annotations

from typing import Callable, Protocol


class ExtractionProvider(Protocol):
    def extract_products(
        self,
        file_bytes: bytes,
        mime_type: str,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> tuple[list[dict], list[str]]:
        """Return (products, retry_errors). Products may be empty on total failure."""
        ...
