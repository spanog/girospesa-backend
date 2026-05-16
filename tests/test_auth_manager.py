"""Unit tests for new auth deps in core/auth.py — manager role."""

from __future__ import annotations

import sys
import os
import types
import asyncio
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub infrastructure modules (must come before any import of core.*)
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_config_mod.settings = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

# Stub core.session so it is never imported with mocked jose (which would
# pollute test_backend_session.py when both files run in the same session).
_session_mod = types.ModuleType("core.session")
_session_mod.read_session_token = MagicMock(return_value=None)  # type: ignore[attr-defined]
_session_mod.set_session_cookie = MagicMock()  # type: ignore[attr-defined]
_session_mod.clear_session_cookie = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.session"] = _session_mod

# Force reload core.auth so we always get the real module, not a stub
# placed by another test file earlier in the session.
if "core.auth" in sys.modules:
    del sys.modules["core.auth"]

import core.auth as _auth_module

import pytest
from fastapi import HTTPException


def _run(coro):
    """Run async coroutine in test."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# require_admin_or_manager
# ---------------------------------------------------------------------------

class TestRequireAdminOrManager:
    def test_accepts_admin(self):
        profile = {"id": "u1", "role": "admin", "managed_supermarket_id": None}
        result = _run(_auth_module.require_admin_or_manager(profile))
        assert result == profile

    def test_accepts_supermarket_manager(self):
        profile = {"id": "u2", "role": "supermarket_manager", "managed_supermarket_id": "sup-1"}
        result = _run(_auth_module.require_admin_or_manager(profile))
        assert result == profile

    def test_rejects_customer_with_403(self):
        profile = {"id": "u3", "role": "customer", "managed_supermarket_id": None}
        with pytest.raises(HTTPException) as exc_info:
            _run(_auth_module.require_admin_or_manager(profile))
        assert exc_info.value.status_code == 403


class TestDecodeToken:
    def test_uses_shared_secret_for_hs256(self, monkeypatch: pytest.MonkeyPatch):
        _config_mod.settings.supabase_jwt_secret = "secret"
        decode = MagicMock(return_value={"sub": "u1"})
        header = MagicMock(return_value={"alg": "HS256"})
        monkeypatch.setattr(_auth_module.jwt, "decode", decode)
        monkeypatch.setattr(_auth_module.jwt, "get_unverified_header", header)

        result = _auth_module._decode_token("token")

        assert result == {"sub": "u1"}
        decode.assert_called_once_with(
            "token",
            "secret",
            algorithms=["HS256"],
            options={"verify_aud": False},
        )

    def test_uses_jwks_for_es256(self, monkeypatch: pytest.MonkeyPatch):
        decode = MagicMock(return_value={"sub": "u2"})
        header = MagicMock(return_value={"alg": "ES256"})
        monkeypatch.setattr(_auth_module.jwt, "decode", decode)
        monkeypatch.setattr(_auth_module.jwt, "get_unverified_header", header)
        monkeypatch.setattr(_auth_module, "_load_jwks", lambda: {"keys": [{"kid": "k1"}]})

        result = _auth_module._decode_token("token")

        assert result == {"sub": "u2"}
        decode.assert_called_once_with(
            "token",
            {"keys": [{"kid": "k1"}]},
            algorithms=["ES256"],
            options={"verify_aud": False},
        )


# ---------------------------------------------------------------------------
# assert_flyer_access
# ---------------------------------------------------------------------------

class TestAssertFlyerAccess:
    def test_admin_unrestricted(self):
        profile = {"role": "admin", "managed_supermarket_id": None}
        flyer = {"supermarket_id": "any-supermarket"}
        _auth_module.assert_flyer_access(profile, flyer)  # Should not raise

    def test_manager_own_supermarket_ok(self):
        profile = {"role": "supermarket_manager", "managed_supermarket_id": "sup-1"}
        flyer = {"supermarket_id": "sup-1"}
        _auth_module.assert_flyer_access(profile, flyer)  # Should not raise

    def test_manager_wrong_supermarket_raises_403(self):
        profile = {"role": "supermarket_manager", "managed_supermarket_id": "sup-1"}
        flyer = {"supermarket_id": "sup-other"}
        with pytest.raises(HTTPException) as exc_info:
            _auth_module.assert_flyer_access(profile, flyer)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Cookie session auth
# ---------------------------------------------------------------------------

class TestCookieAuth:
    def test_cookie_session_authenticates_user(self, monkeypatch):
        from fastapi import FastAPI, Depends
        from fastapi.testclient import TestClient

        monkeypatch.setattr(
            _auth_module,
            "read_session_token",
            lambda token: {"sub": "user-1", "email": "mario@example.com", "role": "customer"},
        )

        app = FastAPI()

        @app.get("/protected")
        async def protected(user_id: str = Depends(_auth_module.get_current_user_id)):
            return {"user_id": user_id}

        client = TestClient(app)
        response = client.get("/protected", cookies={"girospesa_session": "any-token"})

        assert response.status_code == 200
        assert response.json() == {"user_id": "user-1"}

    def test_bearer_compatibility_still_works(self, monkeypatch):
        _config_mod.settings.supabase_jwt_secret = "secret"
        decode = MagicMock(return_value={"sub": "bearer-user"})
        header = MagicMock(return_value={"alg": "HS256"})
        monkeypatch.setattr(_auth_module.jwt, "decode", decode)
        monkeypatch.setattr(_auth_module.jwt, "get_unverified_header", header)

        from fastapi import FastAPI, Depends
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/protected")
        async def protected(user_id: str = Depends(_auth_module.get_current_user_id)):
            return {"user_id": user_id}

        client = TestClient(app)
        response = client.get("/protected", headers={"Authorization": "Bearer test-token"})

        assert response.status_code == 200
        assert response.json() == {"user_id": "bearer-user"}
