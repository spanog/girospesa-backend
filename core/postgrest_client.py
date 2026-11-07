"""Pooled HTTP/1.1 transport for concurrent synchronous PostgREST workers."""
from __future__ import annotations

from httpx import Timeout
from postgrest import SyncPostgrestClient
from postgrest.utils import SyncClient


class WorkerPostgrestClient(SyncPostgrestClient):
    """Avoid sharing HTTP/2 stream failures across independent startup reads."""

    def create_session(
        self,
        base_url: str,
        headers: dict[str, str],
        timeout: int | float | Timeout,
        verify: bool = True,
        proxy: str | None = None,
    ) -> SyncClient:
        return SyncClient(
            base_url=base_url, headers=headers, timeout=timeout,
            verify=verify, proxy=proxy, follow_redirects=True, http2=False,
        )
