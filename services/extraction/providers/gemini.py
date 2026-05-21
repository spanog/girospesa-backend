"""Google Gemini extraction provider."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable

from services.extraction.pdf_utils import PdfChunk, split_pdf_into_chunks
from services.extraction.providers.base import PdfChunkExtractionError
from services.extraction.providers.prompts import EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

MAX_INLINE_BYTES = 20 * 1024 * 1024
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2
SERVER_OVERLOAD_BACKOFF_S = 20
PDF_CHUNK_SIZE_PAGES = 3
_RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'(\d+)s'")
_UNAVAILABLE_RE = re.compile(r"503|UNAVAILABLE", re.IGNORECASE)


def _retry_delay(exc: Exception, attempt: int = 0) -> float:
    m = _RETRY_DELAY_RE.search(str(exc))
    if m:
        return float(m.group(1))
    if _UNAVAILABLE_RE.search(str(exc)):
        return SERVER_OVERLOAD_BACKOFF_S * (2**attempt)
    return RETRY_BACKOFF_S


def _trim_text(value: object, limit: int = 280) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _serialize_error_value(value: object) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=True, sort_keys=True)
        except TypeError:
            return repr(value)
    return str(value)


def _extract_request_id(headers: object) -> str | None:
    if not hasattr(headers, "get"):
        return None
    for key in ("x-request-id", "x-goog-request-id"):
        value = headers.get(key) or headers.get(key.upper())
        if value:
            return str(value)
    return None


def _response_details(response: object) -> list[str]:
    parts: list[str] = []
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        parts.append(f"http_status={status_code}")
    request_id = _extract_request_id(getattr(response, "headers", None))
    if request_id:
        parts.append(f"request_id={request_id}")
    body = getattr(response, "text", None) or getattr(response, "body", None)
    if body:
        parts.append(f"response={_trim_text(_serialize_error_value(body))}")
    return parts


def _format_exception(exc: Exception) -> str:
    parts = [f"type={exc.__class__.__name__}", f"error={_trim_text(exc)}"]
    for attr in ("code", "status", "message"):
        value = getattr(exc, attr, None)
        if value is not None:
            parts.append(f"{attr}={_trim_text(value)}")
    details = getattr(exc, "details", None)
    if details:
        parts.append(f"details={_trim_text(_serialize_error_value(details))}")
    response = getattr(exc, "response", None)
    if response is not None:
        parts.extend(_response_details(response))
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        parts.append(f"cause={cause.__class__.__name__}: {_trim_text(cause)}")
    return " | ".join(parts)


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemma-4-31b-it") -> None:
        self._api_key = api_key
        self._model = model
        self.chunk_size_pages = PDF_CHUNK_SIZE_PAGES

    def extract_products(
        self,
        file_bytes: bytes,
        mime_type: str,
        progress_callback: Callable[[dict], None] | None = None,
        chunk_result_callback: Callable[[dict], None] | None = None,
        start_chunk_index: int = 1,
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
                progress_callback=progress_callback,
                chunk_result_callback=chunk_result_callback,
                start_chunk_index=start_chunk_index,
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
                msg = (
                    f"Attempt {attempt + 1}/{MAX_RETRIES} failed: "
                    f"{_format_exception(exc)}"
                )
                logger.warning(msg)
                retry_errors.append(msg)
                if attempt < MAX_RETRIES - 1:
                    delay = _retry_delay(exc, attempt)
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
        progress_callback: Callable[[dict], None] | None,
        chunk_result_callback: Callable[[dict], None] | None,
        start_chunk_index: int,
    ) -> tuple[list[dict], list[str]]:
        chunks = split_pdf_into_chunks(pdf_bytes, PDF_CHUNK_SIZE_PAGES)
        products: list[dict] = []
        retry_errors: list[str] = []

        if start_chunk_index < 1 or start_chunk_index > len(chunks):
            raise ValueError(
                f"Invalid start_chunk_index {start_chunk_index} for {len(chunks)} chunk(s)"
            )

        for chunk_index, chunk in enumerate(chunks, start=1):
            if chunk_index < start_chunk_index:
                continue
            if progress_callback:
                progress = {
                    "chunks_completed": chunk_index - 1,
                    "chunks_total": len(chunks),
                    "current_chunk_start": chunk.start_page,
                    "current_chunk_end": chunk.end_page,
                    "pages_processed": chunk.start_page - 1,
                    "products_found": len(products),
                }
                if chunk_index == 1:
                    progress["progress_percent"] = 5
                progress_callback(progress)
            chunk_products, chunk_errors = self._extract_pdf_chunk(
                client=client,
                gtypes=gtypes,
                chunk=chunk,
                chunk_index=chunk_index,
                chunks_total=len(chunks),
            )
            retry_errors.extend(chunk_errors)
            if chunk_errors and not chunk_products:
                raise PdfChunkExtractionError(
                    chunk_index=chunk_index,
                    chunks_total=len(chunks),
                    start_page=chunk.start_page,
                    end_page=chunk.end_page,
                    retry_errors=chunk_errors,
                )
            if chunk_result_callback:
                chunk_result_callback(
                    {
                        "chunk_index": chunk_index,
                        "chunks_total": len(chunks),
                        "current_chunk_start": chunk.start_page,
                        "current_chunk_end": chunk.end_page,
                        "products": chunk_products,
                        "retry_errors": chunk_errors,
                    }
                )
            products.extend(chunk_products)
            if progress_callback:
                progress_callback(
                    {
                        "chunks_completed": chunk_index,
                        "chunks_total": len(chunks),
                        "current_chunk_start": chunk.start_page,
                        "current_chunk_end": chunk.end_page,
                        "pages_processed": chunk.end_page,
                        "products_found": len(products),
                    }
                )

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
                msg = (
                    f"{label} attempt {attempt + 1}/{MAX_RETRIES} failed: "
                    f"{_format_exception(exc)}"
                )
                logger.warning(msg)
                retry_errors.append(msg)
                if attempt < MAX_RETRIES - 1:
                    delay = _retry_delay(exc, attempt)
                    if delay > RETRY_BACKOFF_S:
                        logger.info("Rate limited — waiting %.0fs before retry", delay)
                    time.sleep(delay)
                continue
            products = data.get("products", [])
            return [p for p in products if isinstance(p, dict)], retry_errors
        return [], retry_errors
