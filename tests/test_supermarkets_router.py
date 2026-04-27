from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_db_mod = types.ModuleType("core.database")
_db_mod.get_supabase = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.database"] = _db_mod

from fastapi import FastAPI
import httpx
import pytest

from api.routers.supermarkets import router

test_app = FastAPI()
test_app.include_router(router, prefix="/supermarkets")


async def _get(url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


@pytest.mark.asyncio
async def test_lat_lng_returns_postgis_nearby_supermarkets():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-1", "distance_km": 1.2}]
    )
    sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(
        data=[{"id": "sm-1", "name": "Lidl", "is_active": True}]
    )

    with patch("api.routers.supermarkets.get_supabase", return_value=sb):
        resp = await _get("/supermarkets?lat=45.464&lng=9.189&max_distance_km=10")

    assert resp.status_code == 200
    assert resp.json() == [
        {"id": "sm-1", "name": "Lidl", "is_active": True, "distance_km": 1.2}
    ]
    sb.rpc.assert_called_once_with(
        "nearby_supermarkets",
        {
            "user_lat": 45.464,
            "user_lng": 9.189,
            "radius_m": 10000.0,
        },
    )
