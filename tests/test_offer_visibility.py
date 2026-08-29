"""Unit tests for the shared offer-validity rule."""

from datetime import date, datetime

from services.offer_visibility import offer_is_current


def test_offer_is_current_includes_validity_boundaries():
    today = date(2026, 8, 26)
    offer = {"valid_from": "2026-08-26", "valid_to": "2026-08-26"}

    assert offer_is_current(offer, today) is True


def test_offer_is_current_rejects_future_and_expired_windows():
    today = date(2026, 8, 26)

    assert offer_is_current({"valid_from": "2026-08-27"}, today) is False
    assert offer_is_current({"valid_to": "2026-08-25"}, today) is False


def test_offer_is_current_ignores_legacy_active_cache():
    offer = {
        "valid_from": "2026-08-01",
        "valid_to": "2026-08-31",
        "is_active": False,
    }

    assert offer_is_current(offer, date(2026, 8, 26)) is True


def test_offer_is_current_accepts_database_date_values():
    offer = {"valid_from": datetime(2026, 8, 26, 9, 0)}

    assert offer_is_current(offer, date(2026, 8, 26)) is True
