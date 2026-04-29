"""Google Gemini extraction provider.

Sends the whole file (PDF or image) in a single API call.
Gemini supports application/pdf inline up to 20 MB.
"""

from __future__ import annotations

import json
import logging
import re
import time

from services.extraction.providers.prompts import EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

MAX_INLINE_BYTES = 20 * 1024 * 1024
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2
_RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'(\d+)s'")


def _retry_delay(exc: Exception) -> float:
    m = _RETRY_DELAY_RE.search(str(exc))
    return float(m.group(1)) if m else RETRY_BACKOFF_S


class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemma-4-31b-it") -> None:
        self._api_key = api_key
        self._model = model

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
        retry_errors: list[str] = []

        for attempt in range(MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model=self._model,
                    contents=[
                        gtypes.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                        gtypes.Part.from_text(text=EXTRACTION_PROMPT),
                    ],
                    config=gtypes.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                data = json.loads(response.text or "{}")
                products = data.get("products", [])
                return [p for p in products if isinstance(p, dict)], retry_errors
            except json.JSONDecodeError as exc:
                msg = f"Attempt {attempt + 1}/{MAX_RETRIES} — JSON parse error: {exc}"
                logger.warning(msg)
                retry_errors.append(msg)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_S)
            except Exception as exc:
                msg = f"Attempt {attempt + 1}/{MAX_RETRIES} failed: {exc}"
                logger.warning(msg)
                retry_errors.append(msg)
                if attempt < MAX_RETRIES - 1:
                    delay = _retry_delay(exc)
                    if delay > RETRY_BACKOFF_S:
                        logger.info("Rate limited — waiting %.0fs before retry", delay)
                    time.sleep(delay)

        return [], retry_errors
