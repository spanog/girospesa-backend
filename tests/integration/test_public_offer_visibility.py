from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from api.routers.products import router as products_router

app = FastAPI()
app.include_router(products_router, prefix="/products")


@pytest.fixture()
def catalog_context(supabase_client, clean_db):
    store = (
        supabase_client.table("supermarkets")
        .insert(
            {
                "name": "Public Market",
                "slug": f"public-market-{uuid.uuid4().hex[:8]}",
                "lat": 45.0,
                "lng": 9.0,
            }
        )
        .execute()
    ).data[0]
    product = (
        supabase_client.table("products")
        .insert(
            {
                "name": "Yogurt visibile",
                "brand": "Test",
                "category": "alimentari-freschi",
            }
        )
        .execute()
    ).data[0]
    rows = {
        "visible": _insert_offer(
            supabase_client,
            product,
            store,
            is_confirmed=True,
            offer_kind="published_target",
            valid_to="2099-12-31",
            price=1.0,
        ),
        "draft": _insert_offer(
            supabase_client,
            product,
            store,
            is_confirmed=False,
            offer_kind="published_target",
            valid_to="2099-12-31",
            price=2.0,
        ),
        "source": _insert_offer(
            supabase_client,
            product,
            store,
            is_confirmed=True,
            offer_kind="source_master",
            valid_to="2099-12-31",
            price=3.0,
        ),
        "expired": _insert_offer(
            supabase_client,
            product,
            store,
            is_confirmed=True,
            offer_kind="published_target",
            valid_to="2000-01-01",
            price=4.0,
        ),
    }
    return {"store": store, "product": product, "offers": rows}


def _insert_offer(
    supabase_client,
    product: dict,
    store: dict,
    *,
    is_confirmed: bool,
    offer_kind: str,
    valid_to: str,
    price: float,
) -> dict:
    return (
        supabase_client.table("offers")
        .insert(
            {
                "product_id": product["id"],
                "supermarket_id": store["id"],
                "supermarket_name": store["name"],
                "price_offer": price,
                "price_original": price + 1,
                "valid_to": valid_to,
                "is_confirmed": is_confirmed,
                "offer_kind": offer_kind,
            }
        )
        .execute()
    ).data[0]


class TestPublicOfferVisibilityIntegration:
    async def test_public_catalog_exposes_only_current_confirmed_published_offers(
        self,
        catalog_context,
    ):
        visible_offer = catalog_context["offers"]["visible"]
        hidden_offers = [
            catalog_context["offers"]["draft"],
            catalog_context["offers"]["source"],
            catalog_context["offers"]["expired"],
        ]

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            list_resp = await client.get("/products")
            visible_detail_resp = await client.get(f"/products/{visible_offer['id']}")
            hidden_detail_responses = [
                await client.get(f"/products/{offer['id']}") for offer in hidden_offers
            ]

        assert list_resp.status_code == 200
        body = list_resp.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [visible_offer["id"]]

        assert visible_detail_resp.status_code == 200
        assert visible_detail_resp.json()["id"] == visible_offer["id"]
        assert [response.status_code for response in hidden_detail_responses] == [
            404,
            404,
            404,
        ]
