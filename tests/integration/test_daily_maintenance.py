from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from api.routers.ops import router as ops_router
import api.routers.ops as ops_module
from tests.conftest import wait_for_user_bootstrap

app = FastAPI()
app.include_router(ops_router, prefix="/ops")


def _list_item(name: str, *, purchased: bool, purchased_at: str | None = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": 1,
        "checked": purchased,
        "purchased": purchased,
        "purchased_at": purchased_at,
        "source": "manual",
        "pinned_product_id": None,
        "pinned_offer_id": None,
        "found_deals": [],
    }


@pytest.fixture()
def auth_user(supabase_client, clean_db):
    email = f"maintenance_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id = resp.user.id
    wait_for_user_bootstrap(user_id)
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def maintenance_context(supabase_client, auth_user):
    store = (
        supabase_client.table("supermarkets")
        .insert(
            {
                "name": "Maintenance Market",
                "slug": f"maintenance-{uuid.uuid4().hex[:8]}",
                "lat": 45.0,
                "lng": 9.0,
            }
        )
        .execute()
    ).data[0]
    product = (
        supabase_client.table("products")
        .insert({"name": "Pasta cleanup", "brand": "Test"})
        .execute()
    ).data[0]
    expired_flyer = (
        supabase_client.table("flyers")
        .insert(
            {
                "supermarket_id": store["id"],
                "supermarket_name": store["name"],
                "file_url": "https://storage.test/flyers/expired.pdf",
                "file_type": "pdf",
                "status": "done",
                "valid_to": "2000-01-01",
            }
        )
        .execute()
    ).data[0]
    active_flyer = (
        supabase_client.table("flyers")
        .insert(
            {
                "supermarket_id": store["id"],
                "supermarket_name": store["name"],
                "file_url": "https://storage.test/flyers/active.pdf",
                "file_type": "pdf",
                "status": "done",
                "valid_to": "2099-12-31",
            }
        )
        .execute()
    ).data[0]
    expired_offer = _insert_offer(supabase_client, product, store, expired_flyer)
    active_offer = _insert_offer(supabase_client, product, store, active_flyer)
    shopping_list = (
        supabase_client.table("shopping_lists")
        .insert(
            {
                "user_id": auth_user,
                "name": "Cleanup list",
                "items": [
                    _list_item("Vecchio", purchased=True, purchased_at="2000-01-01T00:00:00Z"),
                    _list_item("Da tenere", purchased=False),
                ],
            }
        )
        .execute()
    ).data[0]
    return {
        "expired_offer": expired_offer,
        "active_offer": active_offer,
        "shopping_list": shopping_list,
    }


def _insert_offer(supabase_client, product: dict, store: dict, flyer: dict) -> dict:
    return (
        supabase_client.table("offers")
        .insert(
            {
                "product_id": product["id"],
                "flyer_id": flyer["id"],
                "supermarket_id": store["id"],
                "supermarket_name": store["name"],
                "price_offer": 1.0,
                "price_original": 2.0,
                "valid_to": flyer["valid_to"],
                "is_confirmed": True,
            }
        )
        .execute()
    ).data[0]


class TestDailyMaintenanceIntegration:
    async def test_daily_maintenance_deletes_only_expired_offers_and_old_purchases(
        self,
        supabase_client,
        maintenance_context,
        monkeypatch,
    ):
        monkeypatch.setattr(ops_module.settings, "ops_cron_secret", "test-secret")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            forbidden_resp = await client.post("/ops/cron/daily-maintenance")
            ok_resp = await client.post(
                "/ops/cron/daily-maintenance",
                headers={"x-ops-secret": "test-secret"},
            )

        assert forbidden_resp.status_code == 403
        assert ok_resp.status_code == 200
        assert ok_resp.json()["status"] == "ok"
        assert ok_resp.json()["deleted_offers"] == 1
        assert ok_resp.json()["removed_purchased_items"] == 1

        deleted_offer_rows = (
            supabase_client.table("offers")
            .select("id")
            .eq("id", maintenance_context["expired_offer"]["id"])
            .execute()
        ).data
        active_offer_rows = (
            supabase_client.table("offers")
            .select("id")
            .eq("id", maintenance_context["active_offer"]["id"])
            .execute()
        ).data
        assert deleted_offer_rows == []
        assert active_offer_rows == [{"id": maintenance_context["active_offer"]["id"]}]

        items = (
            supabase_client.table("shopping_lists")
            .select("items")
            .eq("id", maintenance_context["shopping_list"]["id"])
            .single()
            .execute()
        ).data["items"]
        assert [item["name"] for item in items] == ["Da tenere"]
