"""
Geocoding service using Nominatim (OpenStreetMap) via geopy.
Free, no API key required.
"""

import logging
from typing import Optional

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from core.config import settings

logger = logging.getLogger(__name__)

_geocoder: Optional[Nominatim] = None


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
