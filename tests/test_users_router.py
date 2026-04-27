"""Unit tests for api/routers/users.py — body validation and business rules.

These tests cover only the Pydantic request models (pure domain logic).
Infrastructure modules (supabase, jose, etc.) are stubbed so the tests run
without a venv or external services.
"""

import sys
import os
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub out infrastructure modules that are not available in the system Python
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Stub core.config so Settings() doesn't require env vars
_config_mod = types.ModuleType("core.config")
_settings_stub = MagicMock()
_config_mod.settings = _settings_stub  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod

# Stub core.database and core.auth (no DB/JWT calls in these tests)
sys.modules["core.database"] = MagicMock()
sys.modules["core.auth"] = MagicMock()
sys.modules["services.geocoding"] = MagicMock()

import pytest
from pydantic import ValidationError

from api.routers.users import GeocodeBody, UpdateProfileBody, geocode_user_address


class TestUpdateProfileBody:
    """Validate that UpdateProfileBody accepts and rejects the right inputs."""

    def test_all_none_is_valid(self):
        """An empty patch (all fields None) is valid — partial updates are allowed."""
        body = UpdateProfileBody()
        assert body.model_dump(exclude_none=True) == {}

    def test_display_name_only(self):
        body = UpdateProfileBody(display_name="Mario Rossi")
        dumped = body.model_dump(exclude_none=True)
        assert dumped == {"display_name": "Mario Rossi"}

    def test_preferred_supermarkets_list(self):
        body = UpdateProfileBody(preferred_supermarkets=["esselunga", "coop", "lidl"])
        dumped = body.model_dump(exclude_none=True)
        assert dumped["preferred_supermarkets"] == ["esselunga", "coop", "lidl"]

    def test_preferred_supermarkets_empty_list(self):
        body = UpdateProfileBody(preferred_supermarkets=[])
        dumped = body.model_dump(exclude_none=True)
        assert dumped["preferred_supermarkets"] == []

    def test_search_custom_point(self):
        body = UpdateProfileBody(search_label="Ufficio", search_lat=45.4654, search_lng=9.1859)
        dumped = body.model_dump(exclude_none=True)
        assert dumped["search_label"] == "Ufficio"
        assert dumped["search_lat"] == pytest.approx(45.4654)
        assert dumped["search_lng"] == pytest.approx(9.1859)

    def test_max_distance_km_lower_bound(self):
        body = UpdateProfileBody(max_distance_km=1)
        assert body.max_distance_km == 1

    def test_max_distance_km_upper_bound(self):
        body = UpdateProfileBody(max_distance_km=100)
        assert body.max_distance_km == 100

    def test_max_distance_km_below_min_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            UpdateProfileBody(max_distance_km=0)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("max_distance_km",) for e in errors)

    def test_max_distance_km_above_max_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            UpdateProfileBody(max_distance_km=101)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("max_distance_km",) for e in errors)

    def test_notification_flags(self):
        body = UpdateProfileBody(
            notification_expiry=True,
            notification_deals=False,
            notification_favorites=True,
        )
        dumped = body.model_dump(exclude_none=True)
        assert dumped["notification_expiry"] is True
        assert dumped["notification_deals"] is False
        assert dumped["notification_favorites"] is True

    def test_all_address_fields(self):
        body = UpdateProfileBody(
            home_address="Via Roma 1",
            home_city="Milano",
            home_province="MI",
            home_postal_code="20100",
        )
        dumped = body.model_dump(exclude_none=True)
        assert dumped["home_address"] == "Via Roma 1"
        assert dumped["home_city"] == "Milano"
        assert dumped["home_province"] == "MI"
        assert dumped["home_postal_code"] == "20100"

    def test_model_dump_exclude_none_omits_unset_fields(self):
        """Partial patch: only the specified field appears in the dump."""
        body = UpdateProfileBody(display_name="Test")
        dumped = body.model_dump(exclude_none=True)
        assert list(dumped.keys()) == ["display_name"]


class TestGeocodeUserAddress:
    @pytest.mark.asyncio
    async def test_updates_coordinates_and_home_location(self):
        sb = MagicMock()
        update_chain = sb.table.return_value.update.return_value.eq.return_value

        from api.routers import users

        users.geocode_address.return_value = (45.4642, 9.19)
        users.get_supabase.return_value = sb

        result = await geocode_user_address(GeocodeBody(address="Via Roma 1"), "user-1")

        assert result == {"lat": 45.4642, "lng": 9.19}
        sb.table.assert_called_once_with("user_profiles")
        sb.table.return_value.update.assert_called_once_with(
            {
                "home_lat": 45.4642,
                "home_lng": 9.19,
                "home_location": "SRID=4326;POINT(9.19 45.4642)",
            }
        )
        update_chain.execute.assert_called_once()
