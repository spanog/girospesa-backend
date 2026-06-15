"""Unit tests for the backend auth router — login, session, logout."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stubs (before any import of core.* or api.*)
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_config_mod.settings = MagicMock(
    environment="test",
    app_session_cookie_name="girospesa_session",
)
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()


def _set_cookie_side_effect(resp, token, *, secure=False):
    resp.set_cookie("girospesa_session", token, httponly=True, samesite="lax", secure=secure, path="/")


def _clear_cookie_side_effect(resp):
    resp.delete_cookie("girospesa_session", path="/")


_session_mod = types.ModuleType("core.session")
_session_mod.create_session_token = MagicMock(return_value="test-session-token")  # type: ignore[attr-defined]
_session_mod.read_session_token = MagicMock(return_value=None)  # type: ignore[attr-defined]
_session_mod.set_session_cookie = MagicMock(side_effect=_set_cookie_side_effect)  # type: ignore[attr-defined]
_session_mod.clear_session_cookie = MagicMock(side_effect=_clear_cookie_side_effect)  # type: ignore[attr-defined]
sys.modules["core.session"] = _session_mod

if "api.routers.auth" in sys.modules:
    del sys.modules["api.routers.auth"]

import api.routers.auth as _auth_router

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.snapshot_utils import assert_matches_json_snapshot


@pytest.fixture()
def app():
    _app = FastAPI()
    _app.include_router(_auth_router.router)
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

def test_login_sets_http_only_cookie(client, monkeypatch):
    monkeypatch.setattr(
        _auth_router,
        "login_with_password",
        lambda email, password: {
            "user": {
                "id": "user-1",
                "email": "mario@example.com",
                "auth_user_updated_at": "2026-06-15T10:00:00+00:00",
            },
            "profile": {"display_name": "Mario Rossi", "role": "customer"},
        },
    )

    response = client.post("/auth/login", json={
        "email": "mario@example.com",
        "password": "Password123!",
    })

    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "girospesa_session=" in set_cookie
    assert "HttpOnly" in set_cookie


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------

def test_session_returns_guest_without_cookie(client):
    response = client.get("/auth/session")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}


def test_session_with_cookie_fetches_full_profile(client, monkeypatch, request):
    _session_mod.read_session_token.return_value = {
        "sub": "user-1",
        "email": "mario@example.com",
        "role": "customer",
        "auth_user_updated_at": "2026-06-15T10:00:00+00:00",
    }
    fake_profile = {"id": "user-1", "display_name": "Mario Rossi", "role": "customer"}
    fake_sb = MagicMock()
    fake_sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = fake_profile
    monkeypatch.setattr(_auth_router, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(_auth_router, "_validate_backend_session_payload", lambda payload: payload)

    response = client.get("/auth/session", cookies={"girospesa_session": "valid-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"] == {"id": "user-1", "email": "mario@example.com"}
    assert body["profile"]["display_name"] == "Mario Rossi"
    assert_matches_json_snapshot(request, "auth_session_authenticated", body)

    _session_mod.read_session_token.return_value = None  # restore default


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------

def test_logout_clears_cookie(client):
    response = client.post("/auth/logout")
    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    assert "girospesa_session" in set_cookie


def test_exchange_sets_legacy_cookie_from_bearer(app):
    app.dependency_overrides[_auth_router.get_current_user] = lambda: {
        "sub": "user-1",
        "email": "mario@example.com",
    }
    app.dependency_overrides[_auth_router.get_current_user_profile] = lambda: {
        "id": "user-1",
        "role": "customer",
    }
    fake_sb = MagicMock()
    fake_sb.auth.admin.get_user_by_id.return_value.user = MagicMock(
        updated_at="2026-06-15T10:00:00+00:00"
    )
    _auth_router.get_supabase = lambda: fake_sb
    client = TestClient(app, raise_server_exceptions=True)

    response = client.post(
        "/auth/exchange",
        headers={"Authorization": "Bearer token-123"},
    )

    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    assert "girospesa_session=" in set_cookie
    assert "HttpOnly" in set_cookie


# ---------------------------------------------------------------------------
# signup
# ---------------------------------------------------------------------------

_SIGNUP_BODY = {
    "first_name": "Mario",
    "last_name": "Rossi",
    "email": "mario@example.com",
    "password": "Password123!",
    "home_address": "Via Roma 1",
    "home_city": "Milano",
    "home_province": "MI",
    "home_postal_code": "20100",
}


def test_signup_calls_backend_only_flow(client, monkeypatch):
    signup_calls: list[object] = []
    monkeypatch.setattr(
        _auth_router,
        "signup_user",
        lambda body: signup_calls.append(body),
    )

    response = client.post("/auth/signup", json=_SIGNUP_BODY)

    assert response.status_code == 201
    assert len(signup_calls) == 1


def test_signup_returns_duplicate_email_detail(client, monkeypatch):
    fake_sb = MagicMock()
    fake_sb.auth.sign_up.side_effect = Exception("User already registered")
    monkeypatch.setattr(_auth_router, "get_supabase", lambda: fake_sb)

    response = client.post("/auth/signup", json=_SIGNUP_BODY)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Registrazione non riuscita. Verifica i dati inseriti oppure accedi se hai già un account.",
    }


def test_signup_returns_upstream_error_detail(client, monkeypatch):
    fake_sb = MagicMock()
    fake_sb.auth.sign_up.side_effect = Exception("Weak password")
    monkeypatch.setattr(_auth_router, "get_supabase", lambda: fake_sb)

    response = client.post("/auth/signup", json=_SIGNUP_BODY)

    assert response.status_code == 400
    assert response.json() == {"detail": "Password non valida"}


# ---------------------------------------------------------------------------
# reset-password
# ---------------------------------------------------------------------------

def test_reset_password_requires_backend_recovery_token(client):
    _session_mod.read_session_token.return_value = None  # bad token → None

    response = client.post("/auth/reset-password", json={
        "recovery_token": "bad-token",
        "password": "Password123!",
    })

    assert response.status_code == 400


def test_auth_callback_rejects_absolute_next_redirects(client, monkeypatch):
    fake_sb = MagicMock()
    fake_sb.auth.verify_otp.return_value.user = MagicMock(
        id="user-1",
        updated_at="2026-06-15T10:00:00+00:00",
    )
    monkeypatch.setattr(_auth_router, "_fresh_supabase_client", lambda: fake_sb)
    monkeypatch.setattr(_auth_router.settings, "frontend_url", "http://localhost:3000")

    response = client.get(
        "/auth/callback",
        params={
            "token_hash": "valid",
            "type": "recovery",
            "next": "https://evil.example/steal",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "http://localhost:3000/reset-password?token="
    )


def test_reset_password_rejects_replayed_backend_recovery_token(client, monkeypatch):
    _session_mod.read_session_token.return_value = {
        "sub": "user-1",
        "purpose": "password_reset",
        "auth_user_updated_at": "2026-06-15T10:00:00+00:00",
    }
    fake_sb = MagicMock()
    fake_sb.auth.admin.get_user_by_id.return_value.user = MagicMock(
        updated_at="2026-06-15T10:00:01+00:00"
    )
    monkeypatch.setattr(_auth_router, "_fresh_supabase_client", lambda: fake_sb)

    response = client.post(
        "/auth/reset-password",
        json={"recovery_token": "token", "password": "Password123!"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid or expired recovery token"}
