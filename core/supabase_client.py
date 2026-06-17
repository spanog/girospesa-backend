"""Supabase client helpers with support for opaque API keys."""

from __future__ import annotations

import re
from typing import Any

try:
    from supabase import Client
    from supabase._sync.client import SyncClient
    from supabase.lib.client_options import ClientOptions
except Exception:  # pragma: no cover - import-safe fallback for heavily mocked tests
    Client = Any  # type: ignore[misc,assignment]
    ClientOptions = Any  # type: ignore[misc,assignment]
    SyncClient = object  # type: ignore[misc,assignment]

_OPAQUE_KEY_PREFIXES = ("sb_publishable_", "sb_secret_")


def is_opaque_supabase_key(value: str) -> bool:
    return any(value.startswith(prefix) for prefix in _OPAQUE_KEY_PREFIXES)


class CompatibleSyncClient(SyncClient):
    """Sync client that accepts both legacy JWT and modern opaque API keys."""

    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        options: ClientOptions | None = None,
    ):
        if not is_opaque_supabase_key(supabase_key):
            super().__init__(supabase_url, supabase_key, options)
            return

        if not supabase_url:
            raise ValueError("supabase_url is required")
        if not supabase_key:
            raise ValueError("supabase_key is required")
        if not re.match(r"^(https?)://.+", supabase_url):
            raise ValueError("Invalid URL")

        if options is None:
            options = ClientOptions()

        self.supabase_url = supabase_url
        self.supabase_key = supabase_key
        self.options = options
        options.headers.update(self._get_auth_headers())
        self.rest_url = f"{supabase_url}/rest/v1"
        self.realtime_url = f"{supabase_url}/realtime/v1".replace("http", "ws")
        self.auth_url = f"{supabase_url}/auth/v1"
        self.storage_url = f"{supabase_url}/storage/v1"
        self.functions_url = f"{supabase_url}/functions/v1"

        self.auth = self._init_supabase_auth_client(
            auth_url=self.auth_url,
            client_options=options,
        )
        self.realtime = self._init_realtime_client(
            realtime_url=self.realtime_url,
            supabase_key=self.supabase_key,
            options=options.realtime if options else None,
        )
        self._postgrest = None
        self._storage = None
        self._functions = None
        self.auth.on_auth_state_change(self._listen_to_auth_events)


def create_supabase_client(
    supabase_url: str,
    supabase_key: str,
    options: ClientOptions | None = None,
) -> Client:
    if not hasattr(CompatibleSyncClient, "create"):
        raise RuntimeError("supabase client dependencies are unavailable")
    return CompatibleSyncClient.create(
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        options=options,
    )
