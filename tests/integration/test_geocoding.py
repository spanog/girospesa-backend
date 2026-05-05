"""Integration tests — geocoding service → user_profiles home_lat/home_lng.

The geopy Nominatim geocoder is always mocked to avoid real HTTP calls to Nominatim
(works offline). Requires `supabase start` (local Supabase stack) for endpoint
tests that persist coordinates to the DB.

Run:
    supabase start
    pytest tests/integration/test_geocoding.py -v
"""

from __future__ import annotations

import uuid
import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

sys.modules.pop("api.routers.users", None)
sys.modules.pop("services.geocoding", None)

import services.geocoding as geocoding_service
import api.routers.users as users_module
from api.routers.users import router as users_router
from core.auth import get_current_user_id
from services.geocoding import geocode_address

app = FastAPI()
app.include_router(users_router, prefix="/users")

_MILAN_LAT = 45.4654
_MILAN_LNG = 9.1859


def _mock_location(lat: float = _MILAN_LAT, lng: float = _MILAN_LNG) -> MagicMock:
    loc = MagicMock()
    loc.latitude = lat
    loc.longitude = lng
    return loc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_user(supabase_client):
    """Create a temporary auth user; yield its UUID; delete after test."""
    email = f"test_geo_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


# ---------------------------------------------------------------------------
# Tests — geocode_address() service (geocoder mocked, no Supabase calls)
# ---------------------------------------------------------------------------


class TestGeocodeAddressService:
    """Unit-style tests for geocode_address() with mocked Nominatim geocoder."""

    def test_returns_lat_lng_tuple_on_success(self):
        with patch.object(geocoding_service.settings, "geocoding_provider", "nominatim"):
            with patch.object(geocoding_service, "_get_geocoder") as get_geocoder:
                get_geocoder.return_value = MagicMock(geocode=MagicMock(return_value=_mock_location()))
                result = geocode_address("Via Roma 1, 20100 Milano MI")
        assert result == pytest.approx((_MILAN_LAT, _MILAN_LNG))

    def test_returns_none_when_provider_disabled(self):
        with patch.object(geocoding_service.settings, "geocoding_provider", "disabled"):
            result = geocode_address("Via Roma 1, 20100 Milano MI")
        assert result is None

    def test_coordinates_are_floats(self):
        with patch.object(geocoding_service.settings, "geocoding_provider", "nominatim"):
            with patch.object(geocoding_service, "_get_geocoder") as get_geocoder:
                get_geocoder.return_value = MagicMock(geocode=MagicMock(return_value=_mock_location(45, 9)))
                result = geocode_address("Via Roma 1, Milano")
        assert result is not None
        lat, lng = result
        assert isinstance(lat, float)
        assert isinstance(lng, float)

    def test_returns_none_when_address_not_found(self):
        with patch.object(geocoding_service.settings, "geocoding_provider", "nominatim"):
            with patch.object(geocoding_service, "_get_geocoder") as get_geocoder:
                get_geocoder.return_value = MagicMock(geocode=MagicMock(return_value=None))
                result = geocode_address("indirizzo inesistente xyz 999")
        assert result is None

    def test_different_coordinates_are_returned_correctly(self):
        with patch.object(geocoding_service.settings, "geocoding_provider", "nominatim"):
            with patch.object(geocoding_service, "_get_geocoder") as get_geocoder:
                get_geocoder.return_value = MagicMock(
                    geocode=MagicMock(return_value=_mock_location(41.9028, 12.4964))
                )
                result = geocode_address("Via Condotti 1, Roma")
        assert result == pytest.approx((41.9028, 12.4964))


# ---------------------------------------------------------------------------
# Tests — POST /users/geocode endpoint → persists home_lat/home_lng to DB
# ---------------------------------------------------------------------------


class TestGeocodeEndpoint:
    """Verifies that POST /users/geocode persists coordinates to user_profiles."""

    @pytest.fixture(autouse=True)
    def _override_auth(self, auth_user):
        app.dependency_overrides[get_current_user_id] = lambda: auth_user
        yield
        app.dependency_overrides.clear()

    async def test_geocode_populates_home_lat_lng(self, supabase_client, auth_user):
        """Happy path: geocoding succeeds → home_lat/home_lng written to DB."""
        with patch.object(geocoding_service.settings, "geocoding_provider", "nominatim"):
            with patch.object(geocoding_service, "_get_geocoder") as get_geocoder:
                get_geocoder.return_value = MagicMock(geocode=MagicMock(return_value=_mock_location()))
                with patch.object(users_module, "geocode_address", geocode_address):
                    with patch("api.routers.users.get_supabase", return_value=supabase_client):
                        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                            resp = await client.post(
                                "/users/geocode",
                                json={"address": "Via Roma 1, 20100 Milano MI"},
                            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["lat"] == pytest.approx(_MILAN_LAT)
        assert body["lng"] == pytest.approx(_MILAN_LNG)

        profile = (
            supabase_client
            .table("user_profiles")
            .select("home_lat, home_lng")
            .eq("id", auth_user)
            .single()
            .execute()
            .data
        )
        assert profile["home_lat"] == pytest.approx(_MILAN_LAT)
        assert profile["home_lng"] == pytest.approx(_MILAN_LNG)

    async def test_geocode_returns_null_when_address_not_found(self):
        """When geocoder returns no result, endpoint returns {lat: null, lng: null}."""
        with patch.object(geocoding_service.settings, "geocoding_provider", "nominatim"):
            with patch.object(geocoding_service, "_get_geocoder") as get_geocoder:
                get_geocoder.return_value = MagicMock(geocode=MagicMock(return_value=None))
                with patch.object(users_module, "geocode_address", geocode_address):
                    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                        resp = await client.post(
                            "/users/geocode",
                            json={"address": "indirizzo inesistente xyz 999"},
                        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["lat"] is None
        assert body["lng"] is None

    async def test_geocode_requires_authentication(self):
        """Endpoint rejects unauthenticated requests.

        FastAPI's HTTPBearer returns 403 (not 401) when no Authorization header
        is provided — this is the framework's default for missing bearer credentials.
        """
        app_no_auth = FastAPI()
        app_no_auth.include_router(users_router, prefix="/users")
        # No dependency_overrides → HTTPBearer rejects missing token with 403

        async with httpx.AsyncClient(app=app_no_auth, base_url="http://test") as client:
            resp = await client.post(
                "/users/geocode",
                json={"address": "Via Roma 1, Milano"},
            )

        assert resp.status_code == 403
