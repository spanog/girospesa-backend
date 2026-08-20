"""Request diagnostics that preserve privacy and avoid logging request inputs."""
from __future__ import annotations

import logging
from time import perf_counter
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


_LOGGER = logging.getLogger(__name__)
_SLOW_REQUEST_MS = 1_000


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


def _response_size(response) -> str:
    return response.headers.get("content-length", "unknown")


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Attach timing headers and emit one structured line only for slow requests."""

    def __init__(self, app: ASGIApp, slow_request_ms: int = _SLOW_REQUEST_MS) -> None:
        super().__init__(app)
        self._slow_request_ms = slow_request_ms

    async def dispatch(self, request: Request, call_next):
        started_at = perf_counter()
        request_id = uuid4().hex
        response = await call_next(request)
        duration_ms = (perf_counter() - started_at) * 1_000
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
        self._log_slow_request(request, response, request_id, duration_ms)
        return response

    def _log_slow_request(self, request, response, request_id: str, duration_ms: float) -> None:
        if duration_ms < self._slow_request_ms:
            return
        _LOGGER.warning(
            "slow_request request_id=%s route=%s status=%s duration_ms=%.1f bytes=%s",
            request_id,
            _route_path(request),
            response.status_code,
            duration_ms,
            _response_size(response),
        )
