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
            "user": {"id": "user-1", "email": "mario@example.com"},
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


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------

def test_logout_clears_cookie(client):
    response = client.post("/auth/logout")
    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    assert "girospesa_session" in set_cookie
