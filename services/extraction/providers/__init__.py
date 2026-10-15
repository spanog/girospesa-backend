from __future__ import annotations

from services.extraction.providers.base import ExtractionProvider
from services.extraction.providers.gemini import GeminiProvider
from services.extraction.providers.ollama import OllamaProvider
from services.extraction.providers.ollama_client import OllamaClient

__all__ = ["ExtractionProvider", "GeminiProvider", "OllamaProvider", "get_provider"]


def get_provider(settings: object) -> ExtractionProvider:
    """Return configured extraction provider based on settings.llm_provider.

    Supported values: "gemini" (default) and "ollama" (local vision).
    """
    provider = getattr(settings, "llm_provider", "gemini")
    if not isinstance(provider, str):
        provider = "gemini"
    if provider == "gemini":
        return GeminiProvider(
            api_key=getattr(settings, "google_api_key", ""),
            model=getattr(settings, "gemini_model", "gemma-4-31b-it"),
        )
    if provider == "ollama":
        return _ollama_provider(settings)
    raise ValueError(
        f"Unsupported llm_provider: {provider!r}. Supported: 'gemini', 'ollama'"
    )


def _ollama_provider(settings: object) -> OllamaProvider:
    client = OllamaClient(
        base_url=getattr(settings, "ollama_base_url", "http://127.0.0.1:11434"),
        model=getattr(settings, "ollama_model", "gemma4:31b-mlx"),
        timeout_seconds=getattr(settings, "ollama_timeout_seconds", 600),
        context_tokens=getattr(settings, "ollama_context_tokens", 16384),
        max_output_tokens=getattr(settings, "ollama_max_output_tokens", 8192),
    )
    return OllamaProvider(
        client, image_dpi=getattr(settings, "ollama_image_dpi", 150),
        chunk_size_pages=getattr(settings, "ollama_chunk_size_pages", 1),
    )
