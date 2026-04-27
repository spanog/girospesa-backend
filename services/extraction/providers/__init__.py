from __future__ import annotations

from services.extraction.providers.base import ExtractionProvider
from services.extraction.providers.gemini import GeminiProvider

__all__ = ["ExtractionProvider", "GeminiProvider", "get_provider"]


def get_provider(settings: object) -> ExtractionProvider:
    """Return configured extraction provider based on settings.llm_provider.

    Supported values: "gemini" (default).
    Add a new elif branch here to support additional providers (e.g. "openai").
    """
    provider = getattr(settings, "llm_provider", "gemini")
    if not isinstance(provider, str):
        provider = "gemini"
    if provider == "gemini":
        return GeminiProvider(
            api_key=getattr(settings, "google_api_key", ""),
            model=getattr(settings, "gemini_model", "gemini-2.5-flash"),
        )
    raise ValueError(
        f"Unsupported llm_provider: {provider!r}. Supported: 'gemini'"
    )
