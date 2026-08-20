from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from core.request_timing import RequestTimingMiddleware


@pytest.mark.asyncio
async def test_request_timing_exposes_headers_and_omits_query_values(caplog) -> None:
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware, slow_request_ms=0)

    @app.get("/private")
    async def private() -> dict[str, bool]:
        return {"ok": True}

    caplog.set_level(logging.WARNING, logger="core.request_timing")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/private?email=person@example.com&token=secret")

    assert response.headers["x-request-id"]
    assert response.headers["server-timing"].startswith("app;dur=")
    message = caplog.messages[-1]
    assert "route=/private" in message
    assert "person@example.com" not in message
    assert "secret" not in message
