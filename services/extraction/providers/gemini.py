"""Google Gemini extraction provider."""

from __future__ import annotations

import json
import logging
import multiprocessing
import random
import re
import time
from collections import Counter
from typing import Callable

from services.extraction.pdf_utils import PdfChunk, count_pdf_pages, iter_pdf_chunks, pdf_page_chunk
from services.extraction.providers.base import PdfChunkExtractionError
from services.extraction.providers.prompts import EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

MAX_INLINE_BYTES = 20 * 1024 * 1024
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2
TRANSIENT_ERROR_BACKOFF_S = 10
SERVER_OVERLOAD_BACKOFF_S = 20
RETRY_JITTER_RATIO = 0.25
PDF_CHUNK_SIZE_PAGES = 2
LOW_COVERAGE_MAX_PRODUCTS = 1
LOW_COVERAGE_MIN_SIBLING_PRODUCTS = 4
GEMINI_REQUEST_TIMEOUT_MS = 8 * 60 * 1000
GEMINI_REQUEST_TIMEOUT_S = GEMINI_REQUEST_TIMEOUT_MS / 1000
_RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'(\d+)s'")
_UNAVAILABLE_RE = re.compile(r"503|UNAVAILABLE", re.IGNORECASE)
_TRANSIENT_SERVER_ERROR_RE = re.compile(r"500|502|504|INTERNAL|BAD_GATEWAY|GATEWAY_TIMEOUT", re.IGNORECASE)
LOW_COVERAGE_RETRY_PROMPT = (
    "\n\nControllo qualità: questo PDF contiene una sola pagina. "
    "Rileva in modo esaustivo ogni singola offerta acquistabile visibile, "
    "anche quando più prodotti sono disposti nella stessa griglia. "
    "Non omettere prodotti per raggrupparli o perché appartengono alla stessa categoria."
)


class GeminiRequestTimeoutError(TimeoutError):
    """Raised when one Gemini request exceeds its hard deadline."""


def _generate_in_child(
    api_key: str, model: str, payload_bytes: bytes, mime_type: str, prompt: str, result: object
) -> None:
    try:
        from google import genai
        from google.genai import types as gtypes

        response = genai.Client(
            api_key=api_key,
            http_options=gtypes.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
        ).models.generate_content(
            model=model,
            contents=[gtypes.Part.from_bytes(data=payload_bytes, mime_type=mime_type), gtypes.Part.from_text(text=prompt)],
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.1,
                http_options=gtypes.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
            ),
        )
        result.send(("ok", response.text or "{}"))
    except Exception as exc:
        result.send(("error", _format_exception(exc)))
    finally:
        result.close()


def _generate_with_hard_deadline(
    *, api_key: str, model: str, payload_bytes: bytes, mime_type: str, prompt: str, **_: object
) -> str:
    context = multiprocessing.get_context("spawn")
    received, sent = context.Pipe(duplex=False)
    process = context.Process(
        target=_generate_in_child,
        args=(api_key, model, payload_bytes, mime_type, prompt, sent),
    )
    process.start()
    sent.close()
    try:
        if not received.poll(GEMINI_REQUEST_TIMEOUT_S):
            process.terminate()
            process.join()
            raise GeminiRequestTimeoutError(f"Gemini request exceeded {GEMINI_REQUEST_TIMEOUT_S:.0f}s deadline")
        status, payload = received.recv()
        process.join()
        if status != "ok":
            raise RuntimeError(payload)
        return payload
    finally:
        received.close()
        if process.is_alive():
            process.terminate()
            process.join()


def _retry_delay(exc: Exception, attempt: int = 0) -> float:
    m = _RETRY_DELAY_RE.search(str(exc))
    if m:
        return _with_jitter(float(m.group(1)))
    if _UNAVAILABLE_RE.search(str(exc)):
        return _with_jitter(SERVER_OVERLOAD_BACKOFF_S * (2**attempt))
    if _TRANSIENT_SERVER_ERROR_RE.search(str(exc)):
        return _with_jitter(TRANSIENT_ERROR_BACKOFF_S * (2**attempt))
    return _with_jitter(RETRY_BACKOFF_S)


def _with_jitter(delay: float) -> float:
    jitter = delay * RETRY_JITTER_RATIO
    return max(RETRY_BACKOFF_S, delay + random.uniform(-jitter, jitter))


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
    def __init__(
        self,
        api_key: str,
        model: str = "gemma-4-31b-it",
        request_executor: Callable[..., str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._request_executor = request_executor or _generate_with_hard_deadline
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

        client = genai.Client(
            api_key=self._api_key,
            http_options=gtypes.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
        )
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
        prompt: str = EXTRACTION_PROMPT,
    ) -> dict:
        response_text = self._request_executor(
            api_key=self._api_key,
            client=client,
            gtypes=gtypes,
            model=self._model,
            payload_bytes=payload_bytes,
            mime_type=mime_type,
            prompt=prompt,
        )
        return json.loads(response_text)

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
        products: list[dict] = []
        retry_errors: list[str] = []
        pages_total = count_pdf_pages(pdf_bytes)
        chunks_total = max(1, (pages_total + self.chunk_size_pages - 1) // self.chunk_size_pages)

        if start_chunk_index < 1 or start_chunk_index > chunks_total:
            raise ValueError(
                f"Invalid start_chunk_index {start_chunk_index} for {chunks_total} chunk(s)"
            )

        for chunk_index, chunk in enumerate(iter_pdf_chunks(pdf_bytes, self.chunk_size_pages), start=1):
            if chunk_index < start_chunk_index:
                continue
            if progress_callback:
                progress = {
                    "chunks_completed": chunk_index - 1,
                    "chunks_total": chunks_total,
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
                chunks_total=chunks_total,
            )
            chunk_products, recovery_errors = self._recover_sparse_pages(
                client=client,
                gtypes=gtypes,
                pdf_bytes=pdf_bytes,
                chunk=chunk,
                chunk_index=chunk_index,
                chunks_total=chunks_total,
                products=chunk_products,
            )
            chunk_errors.extend(recovery_errors)
            retry_errors.extend(chunk_errors)
            if chunk_errors and not chunk_products:
                raise PdfChunkExtractionError(
                    chunk_index=chunk_index,
                    chunks_total=chunks_total,
                    start_page=chunk.start_page,
                    end_page=chunk.end_page,
                    retry_errors=chunk_errors,
                )
            if chunk_result_callback:
                chunk_result_callback(
                    {
                        "chunk_index": chunk_index,
                        "chunks_total": chunks_total,
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
                        "chunks_total": chunks_total,
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
        prompt: str = EXTRACTION_PROMPT,
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
                    prompt=prompt,
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
            products = [p for p in data.get("products", []) if isinstance(p, dict)]
            return self._absolute_chunk_pages(products, chunk), retry_errors
        return [], retry_errors

    def _recover_sparse_pages(
        self,
        *,
        client: object,
        gtypes: object,
        pdf_bytes: bytes,
        chunk: PdfChunk,
        chunk_index: int,
        chunks_total: int,
        products: list[dict],
    ) -> tuple[list[dict], list[str]]:
        retry_errors: list[str] = []
        for page in self._sparse_pages(products, chunk):
            recovered, errors = self._extract_pdf_chunk(
                client=client,
                gtypes=gtypes,
                chunk=pdf_page_chunk(pdf_bytes, page),
                chunk_index=chunk_index,
                chunks_total=chunks_total,
                prompt=f"{EXTRACTION_PROMPT}{LOW_COVERAGE_RETRY_PROMPT}",
            )
            retry_errors.extend(errors)
            products = self._replace_page_if_more_complete(products, page, recovered)
        return products, retry_errors

    def _sparse_pages(self, products: list[dict], chunk: PdfChunk) -> list[int]:
        if chunk.start_page == chunk.end_page:
            return []
        counts = Counter(product.get("source_page") for product in products)
        maximum = max(counts.values(), default=0)
        if maximum < LOW_COVERAGE_MIN_SIBLING_PRODUCTS:
            return []
        return [
            page for page in range(chunk.start_page, chunk.end_page + 1)
            if counts[page] <= LOW_COVERAGE_MAX_PRODUCTS
        ]

    def _replace_page_if_more_complete(
        self, products: list[dict], page: int, recovered: list[dict]
    ) -> list[dict]:
        previous = [product for product in products if product.get("source_page") == page]
        if len(recovered) <= len(previous):
            logger.warning("Low-coverage retry kept page %d (%d products)", page, len(previous))
            return products
        logger.info("Low-coverage retry recovered page %d (%d → %d products)", page, len(previous), len(recovered))
        retained = [product for product in products if product.get("source_page") != page]
        return retained + recovered

    def _absolute_chunk_pages(self, products: list[dict], chunk: PdfChunk) -> list[dict]:
        normalized: list[dict] = []
        chunk_pages = chunk.end_page - chunk.start_page + 1
        for product in products:
            copy = dict(product)
            page = self._relative_page(copy.get("source_page"), chunk_pages)
            if page is None:
                copy.pop("source_page", None)
                copy.pop("packshot_bbox", None)
            else:
                copy["source_page"] = chunk.start_page + page - 1
            normalized.append(copy)
        return normalized

    def _relative_page(self, value: object, chunk_pages: int) -> int | None:
        try:
            page = int(value)
        except (TypeError, ValueError):
            return None
        return page if 1 <= page <= chunk_pages else None
