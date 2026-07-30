from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers import geocoding
app = FastAPI()
app.include_router(geocoding.router, prefix="/geocoding")


async def get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_address_suggestions_are_exposed_as_public_rest_resource():
    result = SimpleNamespace(
        label="Via Roma, Roma",
        road="Via Roma",
        city="Roma",
        province="RM",
        postal_code="00100",
        lat=41.9,
        lng=12.5,
    )
    with patch.object(geocoding, "lookup_italian_addresses", return_value=[result]):
        response = await get("/geocoding/addresses?query=Via%20Roma")

    assert response.status_code == 200
    assert response.json() == [{
        "label": "Via Roma, Roma",
        "road": "Via Roma",
        "city": "Roma",
        "province": "RM",
        "postalCode": "00100",
        "lat": 41.9,
        "lng": 12.5,
    }]


@pytest.mark.asyncio
async def test_locations_and_reverse_location_use_read_only_resources():
    result = SimpleNamespace(lat=41.9, lng=12.5, label="Roma, 00100")
    with patch.object(geocoding, "search_locations", return_value=[result]):
        locations = await get("/geocoding/locations?query=Roma")
    with patch.object(geocoding, "reverse_geocode", return_value="Roma, 00100"):
        reverse = await get("/geocoding/locations/reverse?lat=41.9&lng=12.5")

    assert locations.status_code == 200
    assert locations.json() == [{"lat": 41.9, "lng": 12.5, "label": "Roma, 00100"}]
    assert reverse.status_code == 200
    assert reverse.json() == {"label": "Roma, 00100"}
