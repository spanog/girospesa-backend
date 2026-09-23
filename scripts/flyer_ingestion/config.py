"""Versioned source configuration for the Codex flyer skill."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml


class FlyerSourceConfigError(ValueError):
    """Raised when a flyer-source YAML configuration is unsafe or incomplete."""


@dataclass(frozen=True)
class FlyerSource:
    """A configured retailer listing and its intended GiroSpesa branches."""

    source_id: str
    listing_url: str
    target_supermarket_ids: tuple[str, ...]
    enabled: bool


def load_sources(path: Path) -> tuple[FlyerSource, ...]:
    """Load and validate a complete source configuration before browser work starts."""
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise FlyerSourceConfigError(f"Cannot read source config: {path}") from exc
    except yaml.YAMLError as exc:
        raise FlyerSourceConfigError("Source config is not valid YAML") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sources"), list):
        raise FlyerSourceConfigError("Source config must contain a sources list")
    sources = tuple(_source_from_mapping(item) for item in parsed["sources"])
    _assert_unique_source_ids(sources)
    return sources


def _source_from_mapping(value: object) -> FlyerSource:
    if not isinstance(value, dict):
        raise FlyerSourceConfigError("Each source must be a mapping")
    source_id = _non_empty_string(value.get("id"), "source id")
    listing_url = _valid_listing_url(value.get("listing_url"))
    targets = _target_ids(value.get("target_supermarket_ids"))
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise FlyerSourceConfigError(f"Source {source_id} enabled must be true or false")
    return FlyerSource(source_id, listing_url, targets, enabled)


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FlyerSourceConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _valid_listing_url(value: object) -> str:
    url = _non_empty_string(value, "listing_url")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise FlyerSourceConfigError("listing_url must be an HTTPS URL")
    return url


def _target_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise FlyerSourceConfigError("target_supermarket_ids must contain at least one UUID")
    target_ids = tuple(_non_empty_string(item, "target supermarket id") for item in value)
    if len(target_ids) != len(set(target_ids)):
        raise FlyerSourceConfigError("target_supermarket_ids must not repeat UUIDs")
    return target_ids


def _assert_unique_source_ids(sources: tuple[FlyerSource, ...]) -> None:
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise FlyerSourceConfigError("Source ids must be unique")
