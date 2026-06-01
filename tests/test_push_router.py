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
from fastapi import FastAPI
import httpx
from pydantic import ValidationError

import api.routers.push as _push_module
from api.routers.push import SubscribeBody, UnsubscribeBody, router as _push_router
from services.push_notify import (
    PushEndpointGoneError,
    PushSubscription,
    notify_extraction_complete,
    notify_public_flyer_published,
    send_push_notification,
)


@pytest.fixture(autouse=True)
def _use_stubbed_webpush(monkeypatch: pytest.MonkeyPatch):
    import services.push_notify as push_notify

    mock_webpush = sys.modules["pywebpush"].webpush
    mock_webpush.reset_mock()
    monkeypatch.setattr(push_notify, "webpush", mock_webpush)
    monkeypatch.setattr(push_notify, "WebPushException", sys.modules["pywebpush"].WebPushException)
    monkeypatch.setattr(push_notify, "settings", _settings_stub)


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


# ── notify_extraction_complete ────────────────────────────────────────────────

def _make_sb_with_subscriptions(subs: list) -> MagicMock:
    sb = MagicMock()
    select_result = MagicMock()
    select_result.data = subs
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = select_result
    delete_result = MagicMock()
    sb.table.return_value.delete.return_value.eq.return_value.execute.return_value = delete_result
    return sb


_SAMPLE_SUB = {
    "endpoint": "https://push.example.com/token",
    "p256dh": "dGVzdA==",
    "auth_key": "YXV0aA==",
}


class TestNotifyExtractionComplete:
    def test_sends_success_push(self):
        sb = _make_sb_with_subscriptions([_SAMPLE_SUB])
        with patch("services.push_notify.send_push_notification") as mock_send:
            notify_extraction_complete(
                sb,
                flyer_id="flyer-42",
                user_id="user-1",
                success=True,
                supermarket_name="Coop",
                products_count=15,
            )
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["title"] == "Estrazione completata"
        assert "15" in call_kwargs["body"]
        assert "Coop" in call_kwargs["body"]
        assert call_kwargs["data"] == {
            "kind": "extraction_complete",
            "flyer_id": "flyer-42",
            "status": "done",
            "products_count": 15,
            "url": "/admin/volantini/flyer-42",
        }

    def test_sends_error_push(self):
        sb = _make_sb_with_subscriptions([_SAMPLE_SUB])
        with patch("services.push_notify.send_push_notification") as mock_send:
            notify_extraction_complete(
                sb,
                flyer_id="flyer-42",
                user_id="user-1",
                success=False,
                supermarket_name="Esselunga",
                error_message="Gemini timeout",
            )
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["title"] == "Estrazione fallita"
        assert "Esselunga" in call_kwargs["body"]
        assert call_kwargs["data"] == {
            "kind": "extraction_failed",
            "flyer_id": "flyer-42",
            "status": "error",
            "products_count": 0,
            "url": "/admin/volantini/flyer-42",
        }

    def test_no_subscriptions_noop(self):
        sb = _make_sb_with_subscriptions([])
        with patch("services.push_notify.send_push_notification") as mock_send:
            notify_extraction_complete(
                sb,
                flyer_id="flyer-1",
                user_id="user-1",
                success=True,
                supermarket_name="Coop",
            )
        mock_send.assert_not_called()


class TestNotifyFavoritesVisibility:
    @pytest.mark.asyncio
    async def test_draft_offer_insert_does_not_notify(self):
        app = FastAPI()
        app.include_router(_push_router, prefix="/push")
        transport = httpx.ASGITransport(app=app)

        sb = MagicMock()
        with patch.object(_push_module, "get_supabase", return_value=sb), \
             patch.object(_push_module, "send_push_notification") as mock_send:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/push/notify-favorites",
                    headers={"x-webhook-secret": "super-secret"},
                    json={"record": {"product_id": "prod-1", "is_confirmed": False}},
                )

        assert resp.status_code == 204
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmed_public_offer_creates_app_notification_without_subscription(self):
        app = FastAPI()
        app.include_router(_push_router, prefix="/push")
        transport = httpx.ASGITransport(app=app)

        tables: dict[str, MagicMock] = {}

        def table(name: str) -> MagicMock:
            return tables[name]

        flyer_table = MagicMock()
        flyer_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
            "is_public": True,
            "status": "done",
        }
        tables["flyers"] = flyer_table

        product_table = MagicMock()
        product_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
            "name": "Parmigiano Reggiano",
        }
        tables["products"] = product_table

        supermarket_table = MagicMock()
        supermarket_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
            "name": "Coop",
        }
        tables["supermarkets"] = supermarket_table

        favorites_table = MagicMock()
        favorites_table.select.return_value.eq.return_value.execute.return_value.data = [
            {"user_id": "user-1"},
        ]
        tables["favorites"] = favorites_table

        profiles_table = MagicMock()
        profiles_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = (
            {"notification_favorites": True}
        )
        tables["user_profiles"] = profiles_table

        subscriptions_table = MagicMock()
        subscriptions_table.select.return_value.eq.return_value.execute.return_value.data = []
        tables["push_subscriptions"] = subscriptions_table

        notifications_table = MagicMock()
        notifications_table.insert.return_value.execute.return_value.data = [{"id": "notif-1"}]
        tables["app_notifications"] = notifications_table

        sb = MagicMock()
        sb.table.side_effect = table

        with patch.object(_push_module, "get_supabase", return_value=sb), \
             patch.object(_push_module, "send_push_notification") as mock_send:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/push/notify-favorites",
                    headers={"x-webhook-secret": "super-secret"},
                    json={
                        "record": {
                            "product_id": "prod-1",
                            "flyer_id": "flyer-1",
                            "supermarket_id": "super-1",
                            "discounted_price": 4.99,
                            "valid_to": "2026-06-30",
                            "is_confirmed": True,
                        },
                    },
                )

        assert resp.status_code == 204
        mock_send.assert_not_called()
        notifications_table.insert.assert_called_once_with(
            {
                "user_id": "user-1",
                "kind": "favorite_offer",
                "title": "Nuova offerta: Parmigiano Reggiano",
                "body": "€4.99 — da Coop — Valida fino al 2026-06-30",
                "data": {
                    "kind": "favorite_offer",
                    "url": "/offerte?product=prod-1",
                    "product_id": "prod-1",
                },
            }
        )

    def test_stale_410_endpoint_deleted(self):
        sb = _make_sb_with_subscriptions([_SAMPLE_SUB])
        with patch(
            "services.push_notify.send_push_notification",
            side_effect=PushEndpointGoneError(_SAMPLE_SUB["endpoint"]),
        ):
            notify_extraction_complete(
                sb,
                flyer_id="flyer-1",
                user_id="user-1",
                success=True,
                supermarket_name="Coop",
            )
        delete_calls = sb.table.return_value.delete.call_args_list
        assert len(delete_calls) >= 1

    def test_push_failure_does_not_raise(self):
        sb = _make_sb_with_subscriptions([_SAMPLE_SUB])
        with patch(
            "services.push_notify.send_push_notification",
            side_effect=RuntimeError("network error"),
        ):
            notify_extraction_complete(
                sb,
                flyer_id="flyer-1",
                user_id="user-1",
                success=True,
                supermarket_name="Coop",
            )

    def test_db_fetch_failure_does_not_raise(self):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.side_effect = RuntimeError("DB error")
        with patch("services.push_notify.send_push_notification") as mock_send:
            notify_extraction_complete(
                sb,
                flyer_id="flyer-1",
                user_id="user-1",
                success=True,
                supermarket_name="Coop",
            )
        mock_send.assert_not_called()


class TestNotifyPublicFlyerPublished:
    def test_notifies_only_nearby_customers_with_deals_enabled(self):
        tables: dict[str, MagicMock] = {}

        def table(name: str) -> MagicMock:
            return tables[name]

        supermarket_table = MagicMock()
        supermarket_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
            "id": "super-1",
            "lat": 45.4642,
            "lng": 9.19,
        }
        tables["supermarkets"] = supermarket_table

        profiles_table = MagicMock()
        profiles_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": "nearby-customer",
                "role": "customer",
                "notification_deals": True,
                "home_lat": 45.465,
                "home_lng": 9.191,
                "search_lat": None,
                "search_lng": None,
                "max_distance_km": 10,
            },
            {
                "id": "far-customer",
                "role": "customer",
                "notification_deals": True,
                "home_lat": 41.9028,
                "home_lng": 12.4964,
                "search_lat": None,
                "search_lng": None,
                "max_distance_km": 10,
            },
        ]
        tables["user_profiles"] = profiles_table

        notifications_table = MagicMock()
        notifications_table.insert.return_value.execute.return_value.data = [{"id": "notif-1"}]
        tables["app_notifications"] = notifications_table

        subscriptions_table = MagicMock()
        subscriptions_table.select.return_value.eq.return_value.execute.side_effect = [
            MagicMock(data=[_SAMPLE_SUB]),
        ]
        subscriptions_table.delete.return_value.eq.return_value.execute.return_value = MagicMock()
        tables["push_subscriptions"] = subscriptions_table

        sb = MagicMock()
        sb.table.side_effect = table

        with patch("services.push_notify.send_push_notification") as mock_send:
            notify_public_flyer_published(
                sb,
                flyer_id="flyer-1",
                supermarket_id="super-1",
                supermarket_name="Coop",
                products_count=12,
            )

        notifications_table.insert.assert_called_once_with(
            {
                "user_id": "nearby-customer",
                "kind": "flyer_published",
                "title": "Nuovo volantino vicino a te",
                "body": "Coop: 12 offerte nuove disponibili vicino a te",
                "data": {
                    "kind": "flyer_published",
                    "flyer_id": "flyer-1",
                    "supermarket_id": "super-1",
                    "products_count": 12,
                    "url": "/volantini",
                },
            }
        )
        mock_send.assert_called_once()

    def test_skips_when_supermarket_coordinates_missing(self):
        tables: dict[str, MagicMock] = {}

        def table(name: str) -> MagicMock:
            return tables[name]

        supermarket_table = MagicMock()
        supermarket_table.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
            "id": "super-1",
            "lat": None,
            "lng": None,
        }
        tables["supermarkets"] = supermarket_table

        sb = MagicMock()
        sb.table.side_effect = table

        with patch("services.push_notify.send_push_notification") as mock_send:
            notify_public_flyer_published(
                sb,
                flyer_id="flyer-1",
                supermarket_id="super-1",
                supermarket_name="Coop",
                products_count=12,
            )

        mock_send.assert_not_called()
