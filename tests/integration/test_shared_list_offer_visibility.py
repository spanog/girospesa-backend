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
        "purchased": False,
        "purchased_by": None,
        "purchased_at": None,
        "added_by": None,
        "added_at": None,
        "source": "manual",
        "pinned_product_id": None,
        "pinned_offer_id": None,
        "category": None,
        "subcategory": None,
        "found_deals": [],
    }


def _create_shared_list(supabase_client, owner_id: str, member_id: str, items: list[dict]) -> dict:
    row = (
        supabase_client.table("shopping_lists")
        .insert({"user_id": owner_id, "name": "Lista condivisa", "items": items})
        .execute()
    ).data[0]
    supabase_client.table("list_members").insert([
        {"list_id": row["id"], "user_id": owner_id, "role": "owner"},
        {"list_id": row["id"], "user_id": member_id, "role": "member"},
    ]).execute()
    return row


@pytest.fixture()
def owner_user(supabase_client, clean_db):
    email = f"test_owner_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    wait_for_user_bootstrap(user_id)
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def member_user(supabase_client):
    email = f"test_member_{uuid.uuid4().hex[:8]}@test.local"
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
            "name": "Market Milano",
            "slug": f"market-milano-{uuid.uuid4().hex[:6]}",
            "lat": 45.4642,
            "lng": 9.19,
        })
        .execute()
    ).data[0]
    product = (
        supabase_client.table("products")
        .insert({
            "name": "Latte intero",
            "brand": "Granarolo",
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
            "price_offer": 1.29,
            "price_original": 1.59,
            "discount_pct": 19,
            "valid_to": _FUTURE_DATE,
            "is_confirmed": True,
        })
        .execute()
    ).data[0]
    return {"store": store, "product": product, "offer": offer}


class TestSharedListOfferVisibility:

    @pytest.fixture(autouse=True)
    def _override_auth(self):
        current_user = {"id": None}
        app.dependency_overrides[get_current_user_id] = lambda: current_user["id"]
        app.dependency_overrides[get_current_access_token] = lambda: "integration-access-token"
        yield current_user
        app.dependency_overrides.clear()

    async def test_member_outside_radius_gets_masked_offer_but_owner_keeps_full_offer(
        self,
        supabase_client,
        owner_user,
        member_user,
        seeded_offer_context,
        _override_auth,
    ):
        supabase_client.table("user_profiles").update({
            "home_lat": 45.4642,
            "home_lng": 9.19,
            "max_distance_km": 10,
        }).eq("id", owner_user).execute()
        supabase_client.table("user_profiles").update({
            "home_lat": 41.9028,
            "home_lng": 12.4964,
            "max_distance_km": 5,
        }).eq("id", member_user).execute()

        item = _manual_item("Latte")
        shopping_list = _create_shared_list(
            supabase_client, owner_user, member_user, [item]
        )

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                _override_auth["id"] = owner_user
                patch_resp = await client.patch(
                    f"/lists/{shopping_list['id']}/items/{item['id']}",
                    json={"pinned_offer_id": seeded_offer_context["offer"]["id"]},
                )
                assert patch_resp.status_code == 200

                owner_resp = await client.get(f"/lists/{shopping_list['id']}")
                _override_auth["id"] = member_user
                member_resp = await client.get(f"/lists/{shopping_list['id']}")

        assert owner_resp.status_code == 200
        assert member_resp.status_code == 200

        owner_item = owner_resp.json()["items"][0]
        assert owner_item["source"] == "offer"
        assert owner_item["pinned_offer_id"] == seeded_offer_context["offer"]["id"]
        assert owner_item["found_deals"][0]["supermarket_name"] == seeded_offer_context["store"]["name"]
        assert owner_item.get("offer_visibility_status") is None

        member_item = member_resp.json()["items"][0]
        assert member_item["source"] == "manual"
        assert member_item["pinned_offer_id"] is None
        assert member_item["pinned_product_id"] == seeded_offer_context["product"]["id"]
        assert member_item["found_deals"] == []
        assert member_item["offer_visibility_status"] == "hidden_for_viewer"

        stored_items = (
            supabase_client.table("shopping_lists")
            .select("items")
            .eq("id", shopping_list["id"])
            .single()
            .execute()
            .data["items"]
        )
        stored_item = stored_items[0]
        assert stored_item["pinned_offer_id"] == seeded_offer_context["offer"]["id"]
        assert stored_item["found_deals"][0]["supermarket_name"] == seeded_offer_context["store"]["name"]

    async def test_deleted_offer_is_projected_as_no_offer_in_list_response(
        self,
        supabase_client,
        owner_user,
        member_user,
        seeded_offer_context,
        _override_auth,
    ):
        item = _manual_item("Latte")
        shopping_list = _create_shared_list(
            supabase_client, owner_user, member_user, [item]
        )

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                _override_auth["id"] = owner_user
                patch_resp = await client.patch(
                    f"/lists/{shopping_list['id']}/items/{item['id']}",
                    json={"pinned_offer_id": seeded_offer_context["offer"]["id"]},
                )
                assert patch_resp.status_code == 200

                supabase_client.table("offers").delete().eq(
                    "id",
                    seeded_offer_context["offer"]["id"],
                ).execute()

                list_resp = await client.get(f"/lists/{shopping_list['id']}")

        assert list_resp.status_code == 200
        projected_item = list_resp.json()["items"][0]
        assert projected_item["source"] == "manual"
        assert projected_item["pinned_offer_id"] is None
        assert projected_item["pinned_product_id"] == seeded_offer_context["product"]["id"]
        assert projected_item["found_deals"] == []
        assert projected_item.get("offer_visibility_status") is None
