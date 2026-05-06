"""Google Gemini extraction provider."""

from __future__ import annotations

import json
import logging
import re
import time

from services.extraction.pdf_utils import PdfChunk, split_pdf_into_chunks
from services.extraction.providers.prompts import EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

MAX_INLINE_BYTES = 20 * 1024 * 1024
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2
PDF_CHUNK_SIZE_PAGES = 3
_RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'(\d+)s'")


def _retry_delay(exc: Exception) -> float:
    m = _RETRY_DELAY_RE.search(str(exc))
    return float(m.group(1)) if m else RETRY_BACKOFF_S


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemma-4-31b-it") -> None:
        self._api_key = api_key
        self._model = model
        self.chunk_size_pages = PDF_CHUNK_SIZE_PAGES

    def extract_products(
        self, file_bytes: bytes, mime_type: str
    ) -> tuple[list[dict], list[str]]:
        """Send file bytes to Gemini; return (products, retry_errors).

        Raises:
            ValueError: file exceeds 20 MB inline limit.
            ImportError: google-genai not installed.
        """
        try:
            from google import genai
            from google.genai import types as gtypes
        except ImportError as exc:
            raise ImportError(
                "google-genai is required. Install with: pip install google-genai"
            ) from exc

        if len(file_bytes) > MAX_INLINE_BYTES:
            raise ValueError(
                f"File too large for inline upload "
                f"({len(file_bytes) / 1_048_576:.1f} MB > 20 MB). "
                "Compress or split the file before extraction."
            )

        client = genai.Client(api_key=self._api_key)
        if mime_type == "application/pdf":
            return self._extract_pdf_chunks(
                client=client,
                gtypes=gtypes,
                pdf_bytes=file_bytes,
            )
        return self._extract_single_payload(
            client=client,
            gtypes=gtypes,
            payload_bytes=file_bytes,
            mime_type=mime_type,
        )

    def _generate_json(
        self,
        *,
        client: object,
        gtypes: object,
        payload_bytes: bytes,
        mime_type: str,
    ) -> dict:
        response = client.models.generate_content(
            model=self._model,
            contents=[
                gtypes.Part.from_bytes(data=payload_bytes, mime_type=mime_type),
                gtypes.Part.from_text(text=EXTRACTION_PROMPT),
            ],
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        return json.loads(response.text or "{}")

    def _extract_single_payload(
        self,
        *,
        client: object,
        gtypes: object,
        payload_bytes: bytes,
        mime_type: str,
    ) -> tuple[list[dict], list[str]]:
        retry_errors: list[str] = []
        for attempt in range(MAX_RETRIES):
            try:
                data = self._generate_json(
                    client=client,
                    gtypes=gtypes,
                    payload_bytes=payload_bytes,
                    mime_type=mime_type,
                )
            except Exception as exc:
                msg = f"Attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}"
                logger.warning(msg)
                retry_errors.append(msg)
                if attempt < MAX_RETRIES - 1:
                    delay = _retry_delay(exc)
                    if delay > RETRY_BACKOFF_S:
                        logger.info("Rate limited — waiting %.0fs before retry", delay)
                    time.sleep(delay)
                continue
            products = data.get("products", [])
            return [p for p in products if isinstance(p, dict)], retry_errors
        return [], retry_errors

    def _extract_pdf_chunks(
        self,
        *,
        client: object,
        gtypes: object,
        pdf_bytes: bytes,
    ) -> tuple[list[dict], list[str]]:
        chunks = split_pdf_into_chunks(pdf_bytes, PDF_CHUNK_SIZE_PAGES)
        products: list[dict] = []
        retry_errors: list[str] = []

        for chunk_index, chunk in enumerate(chunks, start=1):
            chunk_products, chunk_errors = self._extract_pdf_chunk(
                client=client,
                gtypes=gtypes,
                chunk=chunk,
                chunk_index=chunk_index,
                chunks_total=len(chunks),
            )
            retry_errors.extend(chunk_errors)
            if chunk_errors and not chunk_products:
                label = (
                    f"Chunk {chunk_index}/{len(chunks)} "
                    f"(pages {chunk.start_page}-{chunk.end_page})"
                )
                raise ValueError(
                    f"{label} failed after {MAX_RETRIES} attempts"
                )
            products.extend(chunk_products)

        return products, retry_errors

    def _extract_pdf_chunk(
        self,
        *,
        client: object,
        gtypes: object,
        chunk: PdfChunk,
        chunk_index: int,
        chunks_total: int,
    ) -> tuple[list[dict], list[str]]:
        retry_errors: list[str] = []
        label = (
            f"Chunk {chunk_index}/{chunks_total} "
            f"(pages {chunk.start_page}-{chunk.end_page})"
        )
        for attempt in range(MAX_RETRIES):
            try:
                data = self._generate_json(
                    client=client,
                    gtypes=gtypes,
                    payload_bytes=chunk.pdf_bytes,
                    mime_type="application/pdf",
                )
            except Exception as exc:
                msg = f"{label} attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}"
                logger.warning(msg)
                retry_errors.append(msg)
                if attempt < MAX_RETRIES - 1:
                    delay = _retry_delay(exc)
                    if delay > RETRY_BACKOFF_S:
                        logger.info("Rate limited — waiting %.0fs before retry", delay)
                    time.sleep(delay)
                continue
            products = data.get("products", [])
            return [p for p in products if isinstance(p, dict)], retry_errors
        return [], retry_errors
