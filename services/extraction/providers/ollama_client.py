"""HTTP adapter for local Ollama vision inference."""
from __future__ import annotations

import base64
import json

import httpx

from services.extraction.providers.prompts import EXTRACTION_PROMPT
from services.extraction.providers.response_schema import extraction_response_schema
from services.extraction.bounding_box import BoundingBox, InvalidBoundingBox


class OllamaExtractionError(RuntimeError):
    """Ollama could not produce a complete extraction payload."""


class OllamaClient:
    def __init__(
        self, base_url: str, model: str, timeout_seconds: float = 600,
        context_tokens: int = 16384, max_output_tokens: int = 8192,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._context = context_tokens
        self._max_output = max_output_tokens
        self._transport = transport

    def extract(self, image: bytes) -> list[dict]:
        return self.extract_pages([image])

    def extract_pages(self, images: list[bytes]) -> list[dict]:
        if not images:
            raise ValueError("At least one page image is required")
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport, trust_env=False) as client:
                response = client.post(f"{self._base_url}/api/chat", json=self._payload(images))
            if response.is_error:
                raise OllamaExtractionError(f"Ollama HTTP {response.status_code}: {response.text[:500]}")
            return self._parse(response.json())
        except httpx.HTTPError as exc:
            raise OllamaExtractionError(f"Ollama unavailable at {self._base_url}: {exc}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise OllamaExtractionError(f"Invalid Ollama extraction response: {exc}") from exc

    def _payload(self, images: list[bytes]) -> dict:
        return {
            "model": self._model, "stream": False, "think": False, "format": extraction_response_schema(len(images)),
            "keep_alive": "5m",
            "messages": [{"role": "user", "content": _page_prompt(len(images)),
                          "images": [base64.b64encode(image).decode("ascii") for image in images]}],
            "options": {"temperature": 0.1, "num_ctx": self._context, "num_predict": self._max_output},
        }

    @staticmethod
    def _parse(payload: dict) -> list[dict]:
        if not isinstance(payload, dict) or payload.get("done") is not True:
            raise OllamaExtractionError("Incomplete Ollama response")
        if payload.get("done_reason") == "length":
            raise OllamaExtractionError("Ollama output truncated; increase OLLAMA_MAX_OUTPUT_TOKENS")
        content = payload["message"]["content"]
        data = json.loads(content)
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            raise OllamaExtractionError("Ollama response must contain a products array")
        if not all(isinstance(product, dict) for product in data["products"]):
            raise OllamaExtractionError("Ollama products must be objects")
        _validate_product_boxes(data["products"])
        return data["products"]


def _validate_product_boxes(products: list[dict]) -> None:
    for product in products:
        box = product.get("packshot_bbox")
        if box is None:
            continue
        if not isinstance(box, dict):
            raise OllamaExtractionError("packshot_bbox requires named x_min, y_min, x_max, y_max fields")
        try:
            BoundingBox.parse(box)
        except InvalidBoundingBox as exc:
            raise OllamaExtractionError(f"Invalid named product coordinates: {exc}") from exc


def _page_prompt(page_count: int) -> str:
    if page_count == 1:
        return EXTRACTION_PROMPT
    return EXTRACTION_PROMPT + (
        f"\nRicevi {page_count} immagini di pagine consecutive, nell'ordine dell'array images. "
        "La prima immagine è source_page=1, la seconda source_page=2. "
        "Analizza tutte le immagini. Ogni offerta deve indicare source_page relativo alla sua immagine. "
        "Non mescolare descrizioni, prezzi, varianti o box tra pagine diverse. "
        "Le coordinate di ogni box si riferiscono esclusivamente alla pagina indicata."
    )
