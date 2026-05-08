from __future__ import annotations

import json
import sys
import types

from unittest.mock import patch

import pytest


class _FakePart:
    @staticmethod
    def from_bytes(*, data: bytes, mime_type: str) -> dict:
        return {"kind": "bytes", "data": data, "mime_type": mime_type}

    @staticmethod
    def from_text(*, text: str) -> dict:
        return {"kind": "text", "text": text}


class _FakeConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _install_google_stub(fake_client: object) -> None:
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")

    genai_mod.Client = lambda api_key: fake_client
    types_mod.Part = _FakePart
    types_mod.GenerateContentConfig = _FakeConfig
    genai_mod.types = types_mod
    google_mod.genai = genai_mod

    sys.modules["google"] = google_mod
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = types_mod


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    def generate_content(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("No fake Gemini responses left")
        next_response = self._responses.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        return _FakeResponse(next_response)


class _FakeClient:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.models = _FakeModels(responses)


class _FakeHttpResponse:
    def __init__(self, status_code: int, text: str, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class _FakeGeminiError(Exception):
    def __init__(self) -> None:
        super().__init__("500 INTERNAL.")
        self.code = 500
        self.status = "INTERNAL"
        self.message = "Internal error encountered."
        self.details = {"retryable": True, "provider": "gemini"}
        self.response = _FakeHttpResponse(
            500,
            '{"error":{"code":500,"message":"Internal error encountered.","status":"INTERNAL"}}',
            {"x-request-id": "req-123"},
        )


def test_extract_products_chunks_pdf_in_fixed_groups_of_three_pages() -> None:
    fake_client = _FakeClient(
        responses=[
            json.dumps({"products": [{"name": "Prodotto 1", "price_current": 1.0}]}),
            json.dumps({"products": [{"name": "Prodotto 2", "price_current": 2.0}]}),
            json.dumps({"products": [{"name": "Prodotto 3", "price_current": 3.0}]}),
        ]
    )
    _install_google_stub(fake_client)

    from services.extraction.pdf_utils import PdfChunk
    from services.extraction.providers.gemini import GeminiProvider

    chunks = [
        PdfChunk(start_page=1, end_page=3, pdf_bytes=b"chunk-1-3"),
        PdfChunk(start_page=4, end_page=6, pdf_bytes=b"chunk-4-6"),
        PdfChunk(start_page=7, end_page=7, pdf_bytes=b"chunk-7"),
    ]
    progress_events: list[dict] = []
    with patch(
        "services.extraction.providers.gemini.split_pdf_into_chunks",
        return_value=chunks,
    ):
        provider = GeminiProvider(api_key="test-key")
        products, retry_errors = provider.extract_products(
            b"%PDF-fake",
            "application/pdf",
            progress_callback=progress_events.append,
        )

    assert [p["name"] for p in products] == ["Prodotto 1", "Prodotto 2", "Prodotto 3"]
    assert retry_errors == []
    assert len(fake_client.models.calls) == 3
    assert all(call["contents"][0]["mime_type"] == "application/pdf" for call in fake_client.models.calls)
    assert [call["contents"][0]["data"] for call in fake_client.models.calls] == [chunk.pdf_bytes for chunk in chunks]
    assert progress_events == [
        {
            "chunks_completed": 0,
            "chunks_total": 3,
            "current_chunk_start": 1,
            "current_chunk_end": 3,
            "pages_processed": 0,
            "progress_percent": 5,
            "products_found": 0,
        },
        {
            "chunks_completed": 1,
            "chunks_total": 3,
            "current_chunk_start": 1,
            "current_chunk_end": 3,
            "pages_processed": 3,
            "products_found": 1,
        },
        {
            "chunks_completed": 1,
            "chunks_total": 3,
            "current_chunk_start": 4,
            "current_chunk_end": 6,
            "pages_processed": 3,
            "products_found": 1,
        },
        {
            "chunks_completed": 2,
            "chunks_total": 3,
            "current_chunk_start": 4,
            "current_chunk_end": 6,
            "pages_processed": 6,
            "products_found": 2,
        },
        {
            "chunks_completed": 2,
            "chunks_total": 3,
            "current_chunk_start": 7,
            "current_chunk_end": 7,
            "pages_processed": 6,
            "products_found": 2,
        },
        {
            "chunks_completed": 3,
            "chunks_total": 3,
            "current_chunk_start": 7,
            "current_chunk_end": 7,
            "pages_processed": 7,
            "products_found": 3,
        },
    ]


def test_extract_products_aborts_when_one_pdf_chunk_keeps_failing() -> None:
    fake_client = _FakeClient(
        responses=[
            json.dumps({"products": [{"name": "Prodotto 1", "price_current": 1.0}]}),
            RuntimeError("chunk boom"),
            RuntimeError("chunk boom"),
            RuntimeError("chunk boom"),
        ]
    )
    _install_google_stub(fake_client)

    from services.extraction.pdf_utils import PdfChunk
    from services.extraction.providers.gemini import GeminiProvider

    with patch(
        "services.extraction.providers.gemini.split_pdf_into_chunks",
        return_value=[
            PdfChunk(start_page=1, end_page=3, pdf_bytes=b"chunk-1-3"),
            PdfChunk(start_page=4, end_page=6, pdf_bytes=b"chunk-4-6"),
        ],
    ):
        provider = GeminiProvider(api_key="test-key")
        with pytest.raises(ValueError, match=r"Chunk 2/2 \(pages 4-6\) failed after 3 attempts"):
            provider.extract_products(b"%PDF-fake", "application/pdf")

    assert len(fake_client.models.calls) == 4


def test_extract_products_keeps_single_request_for_non_pdf_images() -> None:
    fake_client = _FakeClient(
        responses=[json.dumps({"products": [{"name": "Latte", "price_current": 1.49}]})]
    )
    _install_google_stub(fake_client)

    from services.extraction.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="test-key")
    products, retry_errors = provider.extract_products(b"image-fake", "image/jpeg")

    assert products == [{"name": "Latte", "price_current": 1.49}]
    assert retry_errors == []
    assert len(fake_client.models.calls) == 1


def test_extract_products_logs_structured_gemini_error_details(caplog: pytest.LogCaptureFixture) -> None:
    fake_client = _FakeClient(responses=[_FakeGeminiError(), _FakeGeminiError(), _FakeGeminiError()])
    _install_google_stub(fake_client)

    from services.extraction.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="test-key")
    with caplog.at_level("WARNING"):
        products, retry_errors = provider.extract_products(b"image-fake", "image/jpeg")

    assert products == []
    assert len(retry_errors) == 3
    assert "type=_FakeGeminiError" in retry_errors[0]
    assert "code=500" in retry_errors[0]
    assert "status=INTERNAL" in retry_errors[0]
    assert "message=Internal error encountered." in retry_errors[0]
    assert "request_id=req-123" in retry_errors[0]
    assert "response={\"error\":{\"code\":500,\"message\":\"Internal error encountered.\",\"status\":\"INTERNAL\"}}" in retry_errors[0]
    assert retry_errors[0] in caplog.text
