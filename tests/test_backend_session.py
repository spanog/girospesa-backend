"""Tests for backend-owned session JWT primitives."""

from __future__ import annotations

from types import SimpleNamespace
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import core.session as session


@pytest.fixture(autouse=True)
def clear_session_settings_cache():
    session.get_session_settings.cache_clear()
    yield
    session.get_session_settings.cache_clear()


@pytest.fixture
def stub_session_settings(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        app_session_secret="test-app-session-secret",
        app_session_cookie_name="girospesa_session",
        app_session_ttl_seconds=60 * 60,
    )
    monkeypatch.setattr(session, "get_session_settings", lambda: settings)
    return settings


def test_round_trip_session_token(stub_session_settings) -> None:
    token = session.create_session_token(
        {
            "sub": "user-1",
            "email": "mario@example.com",
            "role": "customer",
        }
    )

    payload = session.read_session_token(token)

    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["email"] == "mario@example.com"
    assert payload["role"] == "customer"


def test_expired_session_token_is_rejected(stub_session_settings) -> None:
    token = session.create_session_token(
        {
            "sub": "user-1",
            "email": "mario@example.com",
            "role": "customer",
        },
        lifetime_seconds=-1,
    )

    assert session.read_session_token(token) is None


def test_invalid_session_token_is_rejected(stub_session_settings) -> None:
    assert session.read_session_token("not-a-token") is None


def test_session_settings_only_require_session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("APP_SESSION_SECRET", "env-session-secret")

    settings = session.get_session_settings()

    assert settings.app_session_secret == "env-session-secret"
    assert settings.app_session_cookie_name == "girospesa_session"
