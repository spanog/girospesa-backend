"""Local extraction contract, without a running Ollama server."""
import base64
import json
from types import SimpleNamespace

import fitz
import httpx
import pytest

from services.extraction.providers.base import PdfChunkExtractionError
from services.extraction.providers.ollama import OllamaProvider
from services.extraction.providers.ollama_client import OllamaClient, OllamaExtractionError


def client_for(handler):
    return OllamaClient("http://localhost:11434", "test-vision", transport=httpx.MockTransport(handler))


def reply(products):
    return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"products": products})}})


def pdf_bytes(pages=3):
    with fitz.open() as document:
        for _ in range(pages):
            document.new_page()
        return document.tobytes()


def test_request_uses_local_vision_json_and_bounded_context():
    def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert payload["messages"][0]["images"] == [base64.b64encode(b"image").decode()]
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["format"]["properties"]["products"]["items"]["properties"]["packshot_bbox"]["anyOf"][0]["required"] == ["x_min", "y_min", "x_max", "y_max"]
        assert payload["options"]["num_ctx"] == 16384
        return reply([{"name": "Latte", "price_current": 1.29}])
    products, errors = OllamaProvider(client_for(handler)).extract_products(b"image", "image/jpeg")
    assert products[0]["source_page"] == 1
    assert errors == []


def test_pdf_streams_checkpoints_and_absolute_pages(tmp_path):
    path = tmp_path / "flyer.pdf"
    path.write_bytes(pdf_bytes())
    chunks, progress = [], []
    provider = OllamaProvider(client_for(lambda request: reply([{"name": "Latte", "source_page": 1}])))
    products, errors = provider.extract_products(path, "application/pdf", progress.append, chunks.append, 2)
    assert products == [] and errors == []
    assert [c["chunk_index"] for c in chunks] == [2, 3]
    assert [c["products"][0]["source_page"] for c in chunks] == [2, 3]
    assert progress[-1]["chunks_completed"] == 3
    assert progress[-1]["pages_processed"] == 3


def test_pdf_honors_saved_chunk_size():
    provider = OllamaProvider(client_for(lambda request: reply([{"name": "Latte"}])))
    provider.chunk_size_pages = 2
    products, _ = provider.extract_products(pdf_bytes(), "application/pdf", start_chunk_index=2)
    assert [p["source_page"] for p in products] == [3]


@pytest.mark.parametrize("content", ['{}', '{"products": [1]}', '[]', 'invalid'])
def test_invalid_response_does_not_silently_succeed(content):
    client = client_for(lambda request: httpx.Response(200, json={"message": {"content": content}, "done": True}))
    with pytest.raises(OllamaExtractionError):
        client.extract(b"image")


def test_truncated_response_rejected():
    client = client_for(lambda request: httpx.Response(200, json={"done": True, "done_reason": "length", "message": {"content": '{"products": []}'}}))
    with pytest.raises(OllamaExtractionError, match="truncated"):
        client.extract(b"image")


def test_connection_failure_keeps_pdf_resume_checkpoint():
    def unavailable(request):
        raise httpx.ConnectError("offline", request=request)
    provider = OllamaProvider(client_for(unavailable))
    with pytest.raises(PdfChunkExtractionError) as error:
        provider.extract_products(pdf_bytes(), "application/pdf", start_chunk_index=2)
    assert error.value.chunk_index == 2
    assert error.value.start_page == 2
    assert "Ollama" in error.value.retry_errors[0]


def test_image_failure_returns_errors():
    provider = OllamaProvider(client_for(lambda request: httpx.Response(404, json={"error": "model missing"})))
    products, errors = provider.extract_products(b"image", "image/png")
    assert products == []
    assert "model missing" in errors[-1]


@pytest.mark.parametrize("start", [0, 4])
def test_invalid_resume_rejected(start):
    provider = OllamaProvider(client_for(lambda request: reply([])))
    with pytest.raises(ValueError, match="start_chunk_index"):
        provider.extract_products(pdf_bytes(), "application/pdf", start_chunk_index=start)


def test_factory_selects_ollama_without_google_key():
    from services.extraction.providers import get_provider
    provider = get_provider(SimpleNamespace(llm_provider="ollama"))
    assert isinstance(provider, OllamaProvider)


def test_ollama_rejects_ambiguous_array_coordinates():
    client = client_for(lambda request: reply([{"name": "Latte", "packshot_bbox": [120, 730, 340, 950]}]))
    with pytest.raises(OllamaExtractionError, match="named"):
        client.extract(b"image")


def test_ollama_accepts_valid_named_coordinates():
    bbox = {"x_min": 730, "y_min": 120, "x_max": 950, "y_max": 340}
    client = client_for(lambda request: reply([{"name": "Latte", "packshot_bbox": bbox}]))
    assert client.extract(b"image")[0]["packshot_bbox"] == bbox


def test_two_pages_share_one_request_and_keep_absolute_page_indices():
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert len(payload["messages"][0]["images"]) == 2
        return reply([{"name": "Latte", "source_page": 2}, {"name": "Pasta", "source_page": 1}])
    provider = OllamaProvider(client_for(handler), chunk_size_pages=2)
    products, errors = provider.extract_products(pdf_bytes(4), "application/pdf", start_chunk_index=2)
    assert len(requests) == 1
    assert [x["source_page"] for x in products] == [4, 3]
    assert errors == []


@pytest.mark.parametrize("page", [None, 0, 3, True])
def test_batch_rejects_ambiguous_page_assignment(page):
    provider = OllamaProvider(client_for(lambda request: reply([{"name": "Latte", "source_page": page}])), chunk_size_pages=2)
    with pytest.raises(PdfChunkExtractionError):
        provider.extract_products(pdf_bytes(2), "application/pdf")


def test_odd_final_page_uses_single_image_and_correct_absolute_page():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return reply([{"name": "Latte"}])
    provider = OllamaProvider(client_for(handler), chunk_size_pages=2)
    products, _ = provider.extract_products(pdf_bytes(3), "application/pdf", start_chunk_index=2)
    assert len(requests[0]["messages"][0]["images"]) == 1
    assert products[0]["source_page"] == 3
