"""
Geocoding service using Nominatim (OpenStreetMap) via geopy.
Free, no API key required.
"""

import logging
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from core.config import settings

logger = logging.getLogger(__name__)

_geocoder: Optional[Nominatim] = None
ADDRESS_TOKEN_COMPLETIONS = ("Roma", "Nazionale", "Milano", "Torino", "Bologna", "Firenze")


@dataclass(frozen=True)
class AddressSuggestion:
    label: str
    road: str
    city: str
    province: str
    postal_code: str
    lat: float | None
    lng: float | None


@dataclass(frozen=True)
class GeocodedLocation:
    lat: float
    lng: float
    label: str


def _get_geocoder() -> Optional[Nominatim]:
    global _geocoder
    if settings.geocoding_provider != "nominatim":
        return None
    if _geocoder is None:
        _geocoder = Nominatim(user_agent="girospesa/1.0")
    return _geocoder


@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((GeocoderTimedOut, GeocoderServiceError)),
    reraise=False,
)
def geocode_address(address: str) -> Optional[tuple[float, float]]:
    """
    Geocode a free-text address.
    Returns (lat, lng) or None if geocoding fails.
    Timeout: 5s. Retries once.
    """
    geocoder = _get_geocoder()
    if geocoder is None:
        logger.info("Geocoding skipped: provider disabled")
        return None
    try:
        location = geocoder.geocode(address, timeout=5)
        if location:
            return float(location.latitude), float(location.longitude)
    except (GeocoderTimedOut, GeocoderServiceError) as exc:
        logger.warning("Geocoding failed for '%s': %s", address, exc)
        raise

    return None


def _address_part(address: dict[str, Any], *keys: str) -> str:
    return next((str(address[key]) for key in keys if address.get(key)), "")


def _location_address(location: Any) -> dict[str, Any]:
    raw = getattr(location, "raw", {})
    return raw.get("address", {}) if isinstance(raw, dict) else {}


def _location_label(location: Any, fallback: str) -> str:
    return str(getattr(location, "address", None) or fallback)


def _search(query: str, limit: int) -> list[Any]:
    geocoder = _get_geocoder()
    if geocoder is None:
        return []
    try:
        results = geocoder.geocode(
            query,
            exactly_one=False,
            limit=limit,
            addressdetails=True,
            country_codes="it",
            timeout=5,
        )
    except (GeocoderTimedOut, GeocoderServiceError) as exc:
        logger.warning("Geocoding search failed for '%s': %s", query, exc)
        return []
    return list(results or [])


def _normalize_token(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _lookup_queries(query: str) -> list[str]:
    trimmed = query.strip()
    if len(trimmed) < 3:
        return []
    tokens = trimmed.split()
    variants = {trimmed}
    for _ in range(2):
        for variant in list(variants):
            parts = variant.split()
            for index, token in enumerate(parts):
                normalized = _normalize_token(token)
                for completion in ADDRESS_TOKEN_COMPLETIONS:
                    if len(token) >= 3 and _normalize_token(completion).startswith(normalized):
                        if _normalize_token(completion) != normalized:
                            completed = [*parts]
                            completed[index] = completion
                            variants.add(" ".join(completed))
    if len(tokens) >= 4:
        variants.add(" ".join(tokens[:-1]))
    return [trimmed, *(variant for variant in variants if variant != trimmed)]


def _to_address_suggestion(location: Any) -> AddressSuggestion | None:
    address = _location_address(location)
    road = _address_part(address, "road", "pedestrian", "footway", "path", "quarter", "suburb")
    city = _address_part(address, "city", "town", "village", "municipality")
    province = _address_part(address, "county", "state_district", "province")
    if not city or not province:
        return None
    return AddressSuggestion(
        label=_location_label(location, f"{road or city}, {city}"),
        road=road or city,
        city=city,
        province=province,
        postal_code=_address_part(address, "postcode"),
        lat=float(getattr(location, "latitude", 0)) or None,
        lng=float(getattr(location, "longitude", 0)) or None,
    )


def lookup_italian_addresses(query: str) -> list[AddressSuggestion]:
    for lookup_query in _lookup_queries(query):
        suggestions: list[AddressSuggestion] = []
        seen: set[tuple[str, str, str, str]] = set()
        for location in _search(lookup_query, limit=5):
            suggestion = _to_address_suggestion(location)
            if suggestion is None:
                continue
            key = (suggestion.road, suggestion.city, suggestion.province, suggestion.postal_code)
            if key not in seen:
                seen.add(key)
                suggestions.append(suggestion)
        if suggestions:
            return suggestions
    return []


def search_locations(query: str) -> list[GeocodedLocation]:
    trimmed = query.strip()
    if len(trimmed) < 2:
        return []
    locations: list[GeocodedLocation] = []
    for location in _search(trimmed, limit=5):
        address = _location_address(location)
        city = _address_part(address, "city", "town", "village")
        postcode = _address_part(address, "postcode")
        road = _address_part(address, "road")
        label = f"{road}, " if road else ""
        label += city or _location_label(location, trimmed)
        if postcode:
            label += f", {postcode}"
        locations.append(
            GeocodedLocation(float(location.latitude), float(location.longitude), label)
        )
    return locations


def reverse_geocode(lat: float, lng: float) -> str:
    fallback = f"{lat:.2f}, {lng:.2f}"
    geocoder = _get_geocoder()
    if geocoder is None:
        return fallback
    try:
        location = geocoder.reverse((lat, lng), exactly_one=True, addressdetails=True, timeout=5)
    except (GeocoderTimedOut, GeocoderServiceError) as exc:
        logger.warning("Reverse geocoding failed for '%s, %s': %s", lat, lng, exc)
        return fallback
    if location is None:
        return fallback
    address = _location_address(location)
    city = _address_part(address, "city", "town", "village")
    postcode = _address_part(address, "postcode")
    return f"{city}, {postcode}" if city and postcode else city or _location_label(location, fallback)
