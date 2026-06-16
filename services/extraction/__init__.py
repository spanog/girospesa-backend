from __future__ import annotations

__all__ = ["ExtractionService"]


def __getattr__(name: str):
    if name != "ExtractionService":
        raise AttributeError(name)
    from services.extraction.service import ExtractionService

    return ExtractionService
