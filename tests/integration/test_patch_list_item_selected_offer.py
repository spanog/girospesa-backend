from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.lists import router as lists_router
from core.auth import get_current_access_token, get_current_user_id
from tests.conftest import wait_for_user_bootstrap

app = FastAPI()
app.include_router(lists_router, prefix="/lists")

_FUTURE_DATE = "2099-12-31"


def _manual_item(name: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": 1,
        "unit": None,
        "checked": False,
        "checked_by": None,
        "checked_at": None,
        "added_by": None,
        "added_at": None,
        "source": "manual",
        "pinned_product_id": None,
        "pinned_offer_id": None,
        "category": None,
        "subcategory": None,
        "found_deals": [],
    }


def _create_member_list(supabase_client, user_id: str, items: list[dict]) -> dict:
    row = (
        supabase_client.table("shopping_lists")
        .insert({"user_id": user_id, "name": "Test list", "items": items})
        .execute()
    ).data[0]
    supabase_client.table("list_members").insert({
        "list_id": row["id"],
        "user_id": user_id,
        "role": "owner",
    }).execute()
    return row


@pytest.fixture()
def auth_user(supabase_client, clean_db):
    email = f"test_patch_item_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    wait_for_user_bootstrap(user_id)
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def seeded_offer_context(supabase_client):
    store = (
        supabase_client.table("supermarkets")
        .insert({
            "name": "Coop Test",
            "slug": f"coop-test-{uuid.uuid4().hex[:6]}",
            "lat": 45.0,
            "lng": 9.0,
        })
        .execute()
    ).data[0]
    product = (
        supabase_client.table("products")
        .insert({
            "name": "Latte scremato",
            "brand": "Berna",
            "category": "alimentari-freschi",
            "subcategory": "Latticini e Formaggi",
        })
        .execute()
    ).data[0]
    offer = (
        supabase_client.table("offers")
        .insert({
            "product_id": product["id"],
            "supermarket_id": store["id"],
            "supermarket_name": store["name"],
            "price_offer": 1.19,
            "price_original": 1.49,
            "discount_pct": 20,
            "unit_price": "1,19 €/l",
            "unit_price_value": 1.19,
            "unit_price_unit": "l",
            "valid_to": _FUTURE_DATE,
            "is_confirmed": True,
        })
        .execute()
    ).data[0]
    return {"store": store, "product": product, "offer": offer}


class TestPatchItemSelectedOfferIntegration:

    @pytest.fixture(autouse=True)
    def _override_auth(self, auth_user):
        app.dependency_overrides[get_current_user_id] = lambda: auth_user
        app.dependency_overrides[get_current_access_token] = lambda: "integration-access-token"
        yield
        app.dependency_overrides.clear()

    async def test_selected_offer_updates_manual_item_coherently(
        self, supabase_client, auth_user, seeded_offer_context
    ):
        item = _manual_item("Latte")
        shopping_list = _create_member_list(supabase_client, auth_user, [item])

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.patch(
                    f"/lists/{shopping_list['id']}/items/{item['id']}",
                    json={"pinned_offer_id": seeded_offer_context["offer"]["id"]},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "offer"
        assert body["pinned_offer_id"] == seeded_offer_context["offer"]["id"]
        assert body["pinned_product_id"] == seeded_offer_context["product"]["id"]
        assert body["category"] == "alimentari-freschi"
        assert body["subcategory"] == "Latticini e Formaggi"
        assert body["found_deals"][0]["offer_id"] == seeded_offer_context["offer"]["id"]
        assert body["found_deals"][0]["product_id"] == seeded_offer_context["product"]["id"]
        assert body["found_deals"][0]["supermarket_name"] == seeded_offer_context["store"]["name"]
        saved_items = (
            supabase_client.table("shopping_lists")
            .select("items")
            .eq("id", shopping_list["id"])
            .single()
            .execute()
            .data["items"]
        )
        saved_item = next(saved for saved in saved_items if saved["id"] == item["id"])
        assert saved_item["pinned_offer_id"] == seeded_offer_context["offer"]["id"]
        assert saved_item["pinned_product_id"] == seeded_offer_context["product"]["id"]
        assert saved_item["found_deals"][0]["offer_id"] == seeded_offer_context["offer"]["id"]

    async def test_missing_offer_returns_404_without_modifying_item(
        self, supabase_client, auth_user
    ):
        item = _manual_item("Latte")
        shopping_list = _create_member_list(supabase_client, auth_user, [item])
        missing_offer_id = str(uuid.uuid4())

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.patch(
                    f"/lists/{shopping_list['id']}/items/{item['id']}",
                    json={"pinned_offer_id": missing_offer_id},
                )

        assert resp.status_code == 404
        items = (
            supabase_client.table("shopping_lists")
            .select("items")
            .eq("id", shopping_list["id"])
            .single()
            .execute()
            .data["items"]
        )
        saved_item = next(saved for saved in items if saved["id"] == item["id"])
        assert saved_item["source"] == "manual"
        assert saved_item["pinned_offer_id"] is None
        assert saved_item["found_deals"] == []

    async def test_quantity_patch_on_other_item_keeps_selected_offer_snapshot(
        self, supabase_client, auth_user, seeded_offer_context
    ):
        offer_item = _manual_item("Latte")
        other_item = _manual_item("Pane")
        shopping_list = _create_member_list(
            supabase_client,
            auth_user,
            [offer_item, other_item],
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                select_resp = await client.patch(
                    f"/lists/{shopping_list['id']}/items/{offer_item['id']}",
                    json={"pinned_offer_id": seeded_offer_context["offer"]["id"]},
                )
                quantity_resp = await client.patch(
                    f"/lists/{shopping_list['id']}/items/{other_item['id']}",
                    json={"quantity": 3},
                )

        assert select_resp.status_code == 200
        assert quantity_resp.status_code == 200
        items = (
            supabase_client.table("shopping_lists")
            .select("items")
            .eq("id", shopping_list["id"])
            .single()
            .execute()
            .data["items"]
        )
        saved_offer_item = next(saved for saved in items if saved["id"] == offer_item["id"])
        saved_other_item = next(saved for saved in items if saved["id"] == other_item["id"])
        assert saved_offer_item["pinned_offer_id"] == seeded_offer_context["offer"]["id"]
        assert saved_offer_item["found_deals"][0]["offer_id"] == seeded_offer_context["offer"]["id"]
        assert saved_other_item["quantity"] == 3
