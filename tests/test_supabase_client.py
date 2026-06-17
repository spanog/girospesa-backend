from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock


def _load_supabase_client_module():
    sys.modules.pop("core.supabase_client", None)
    sys.modules.pop("supabase", None)
    return importlib.import_module("core.supabase_client")


def test_is_opaque_supabase_key_detects_modern_keys() -> None:
    module = _load_supabase_client_module()

    assert module.is_opaque_supabase_key("sb_secret_abc123")
    assert module.is_opaque_supabase_key("sb_publishable_abc123")
    assert not module.is_opaque_supabase_key("eyJhbGciOiJIUzI1NiJ9.abc.def")


def test_compatible_sync_client_accepts_opaque_secret_key(monkeypatch) -> None:
    module = _load_supabase_client_module()
    fake_auth = MagicMock()
    monkeypatch.setattr(
        module.CompatibleSyncClient,
        "_init_supabase_auth_client",
        staticmethod(lambda auth_url, client_options, verify=True, proxy=None: fake_auth),
    )
    monkeypatch.setattr(
        module.CompatibleSyncClient,
        "_init_realtime_client",
        staticmethod(lambda realtime_url, supabase_key, options=None: MagicMock()),
    )

    client = module.CompatibleSyncClient(
        "https://example.supabase.co",
        "sb_secret_test_key",
    )

    assert client.supabase_url == "https://example.supabase.co"
    assert client.supabase_key == "sb_secret_test_key"
    assert client.options.headers["apiKey"] == "sb_secret_test_key"
    assert client.options.headers["Authorization"] == "Bearer sb_secret_test_key"
    fake_auth.on_auth_state_change.assert_called_once()
