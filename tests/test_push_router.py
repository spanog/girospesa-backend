"""Unit tests for api/routers/push.py — Pydantic models and push_notify service.

Infrastructure (supabase, pywebpush, etc.) is stubbed so tests run without
a venv or external services.
"""
from __future__ import annotations

import sys
import os
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Stub infrastructure modules not present in system Python
# ---------------------------------------------------------------------------
for _mod in ("supabase", "jose", "jose.jwt", "pywebpush"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Provide realistic WebPushException stub with a .response attribute
_webpush_mod = sys.modules["pywebpush"]
_webpush_mod.WebPushException = type(
    "WebPushException",
    (Exception,),
    {"__init__": lambda self, msg="", response=None: (
        super(type(self), self).__init__(msg),
        setattr(self, "response", response),
    )[-1] or None},
)
_webpush_mod.webpush = MagicMock()

_config_mod = types.ModuleType("core.config")
_settings_stub = MagicMock()
_settings_stub.vapid_private_key = "test-private-key"
_settings_stub.vapid_mailto = "mailto:test@example.com"
_settings_stub.webhook_secret = "super-secret"
_config_mod.settings = _settings_stub  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod

sys.modules["core.database"] = MagicMock()
sys.modules["core.auth"] = MagicMock()

import pytest
from pydantic import ValidationError

from api.routers.push import SubscribeBody, UnsubscribeBody
from services.push_notify import PushEndpointGoneError, PushSubscription, send_push_notification


# ── SubscribeBody ─────────────────────────────────────────────────────────────

class TestSubscribeBody:
    def test_full_valid_payload(self):
        body = SubscribeBody(
            endpoint="https://push.example.com/abc123",
            p256dh="base64key==",
            auth_key="base64auth==",
            user_agent="Mozilla/5.0",
        )
        assert body.endpoint == "https://push.example.com/abc123"
        assert body.user_agent == "Mozilla/5.0"

    def test_user_agent_is_optional(self):
        body = SubscribeBody(
            endpoint="https://push.example.com/abc123",
            p256dh="base64key==",
            auth_key="base64auth==",
        )
        assert body.user_agent is None

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValidationError):
            SubscribeBody(p256dh="key", auth_key="auth")  # type: ignore[call-arg]

    def test_missing_p256dh_raises(self):
        with pytest.raises(ValidationError):
            SubscribeBody(endpoint="https://example.com", auth_key="auth")  # type: ignore[call-arg]

    def test_missing_auth_key_raises(self):
        with pytest.raises(ValidationError):
            SubscribeBody(endpoint="https://example.com", p256dh="key")  # type: ignore[call-arg]


# ── UnsubscribeBody ───────────────────────────────────────────────────────────

class TestUnsubscribeBody:
    def test_valid_endpoint(self):
        body = UnsubscribeBody(endpoint="https://push.example.com/abc123")
        assert body.endpoint == "https://push.example.com/abc123"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValidationError):
            UnsubscribeBody()  # type: ignore[call-arg]


# ── PushSubscription dataclass ────────────────────────────────────────────────

class TestPushSubscription:
    def test_immutable(self):
        sub = PushSubscription(endpoint="https://example.com", p256dh="key", auth_key="auth")
        with pytest.raises((AttributeError, TypeError)):
            sub.endpoint = "https://other.com"  # type: ignore[misc]


# ── send_push_notification ────────────────────────────────────────────────────

class TestSendPushNotification:
    def _make_sub(self) -> PushSubscription:
        return PushSubscription(
            endpoint="https://push.example.com/token",
            p256dh="dGVzdA==",
            auth_key="YXV0aA==",
        )

    def test_calls_webpush_with_correct_args(self):
        sub = self._make_sub()
        mock_webpush = sys.modules["pywebpush"].webpush
        mock_webpush.reset_mock()

        send_push_notification(sub, title="Test", body="Hello")

        mock_webpush.assert_called_once()
        call_kwargs = mock_webpush.call_args.kwargs
        assert call_kwargs["subscription_info"]["endpoint"] == sub.endpoint
        assert call_kwargs["subscription_info"]["keys"]["p256dh"] == sub.p256dh
        assert call_kwargs["subscription_info"]["keys"]["auth"] == sub.auth_key
        assert call_kwargs["vapid_private_key"] == "test-private-key"
        assert "Test" in call_kwargs["data"]
        assert "Hello" in call_kwargs["data"]

    def test_410_raises_push_endpoint_gone(self):
        sub = self._make_sub()
        WebPushException = sys.modules["pywebpush"].WebPushException
        mock_response = MagicMock()
        mock_response.status_code = 410
        exc = WebPushException("Gone", response=mock_response)

        # Patch the local name inside the push_notify module (not the source module)
        with patch("services.push_notify.webpush", side_effect=exc):
            with pytest.raises(PushEndpointGoneError):
                send_push_notification(sub, title="Test", body="Hello")

    def test_other_error_re_raises_webpush_exception(self):
        sub = self._make_sub()
        WebPushException = sys.modules["pywebpush"].WebPushException
        mock_response = MagicMock()
        mock_response.status_code = 500
        exc = WebPushException("Server error", response=mock_response)

        with patch("services.push_notify.webpush", side_effect=exc):
            with pytest.raises(WebPushException):
                send_push_notification(sub, title="Test", body="Hello")

    def test_notification_payload_contains_data(self):
        sub = self._make_sub()
        mock_webpush = sys.modules["pywebpush"].webpush
        mock_webpush.reset_mock()

        send_push_notification(
            sub,
            title="Nuova offerta",
            body="€1.99",
            data={"url": "/offerte?product=abc"},
        )

        call_kwargs = mock_webpush.call_args.kwargs
        import json
        payload = json.loads(call_kwargs["data"])
        assert payload["title"] == "Nuova offerta"
        assert payload["body"] == "€1.99"
        assert payload["data"]["url"] == "/offerte?product=abc"
