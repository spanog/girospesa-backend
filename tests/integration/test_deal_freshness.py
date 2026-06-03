"""Integration tests — GET /lists/{list_id}/deal-freshness.

Seeds products and offers with valid_to in past/future, then verifies that
the endpoint returns the correct DealFreshnessStatus for each scenario.

Runs against the isolated integration Docker stack.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.lists import router as lists_router
from core.auth import get_current_user_id
from tests.conftest import wait_for_user_bootstrap

app = FastAPI()
app.include_router(lists_router, prefix="/lists")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAST_DATE = "2000-01-01"
_FUTURE_DATE = "2099-12-31"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    name: str,
    pinned_offer_id: str | None = None,
    pinned_product_id: str | None = None,
    pinned_price: float | None = None,
) -> dict:
    item: dict = {
        "id": str(uuid.uuid4()),
        "name": name,
        "checked": False,
        "added_by": None,
        "added_at": None,
        "pinned_product_id": pinned_product_id,
        "pinned_offer_id": pinned_offer_id,
        "found_deals": [],
    }
    if pinned_offer_id and pinned_price is not None:
        item["found_deals"] = [
            {"offer_id": pinned_offer_id, "price_offer": pinned_price}
        ]
    return item


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_user(supabase_client):
    email = f"test_freshness_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    wait_for_user_bootstrap(user_id)
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def supermarket(supabase_client, clean_db):
    row = (
        supabase_client.table("supermarkets")
        .insert({"name": "Test Market", "slug": f"tm-{uuid.uuid4().hex[:6]}", "lat": 45.0, "lng": 9.0})
        .execute()
    ).data[0]
    return row


@pytest.fixture()
def product(supabase_client, supermarket):
    row = (
        supabase_client.table("products")
        .insert({
            "name": "Latte intero",
            "brand": "Granarolo",
        })
        .execute()
    ).data[0]
    return row


def _insert_offer(
    supabase_client, product: dict, supermarket: dict, valid_to: str, price: float = 1.29
) -> dict:
    return (
        supabase_client.table("offers")
        .insert({
            "product_id": product["id"],
            "supermarket_id": supermarket["id"],
            "supermarket_name": supermarket["name"],
            "price_offer": price,
            "price_original": 1.59,
            "valid_to": valid_to,
        })
        .execute()
    ).data[0]


def _create_list(supabase_client, user_id: str, items: list[dict]) -> dict:
    list_row = (
        supabase_client.table("shopping_lists")
        .insert({"user_id": user_id, "name": "Test list", "items": items})
        .execute()
    ).data[0]
    supabase_client.table("list_members").insert({
        "list_id": list_row["id"],
        "user_id": user_id,
        "role": "owner",
    }).execute()
    return list_row


def _set_profile_location(
    supabase_client,
    user_id: str,
    *,
    lat: float,
    lng: float,
    max_distance_km: int,
) -> None:
    (
        supabase_client.table("user_profiles")
        .update({
            "home_lat": lat,
            "home_lng": lng,
            "max_distance_km": max_distance_km,
        })
        .eq("id", user_id)
        .execute()
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDealFreshnessIntegration:

    @pytest.fixture(autouse=True)
    def _override_auth(self, auth_user):
        app.dependency_overrides[get_current_user_id] = lambda: auth_user
        yield
        app.dependency_overrides.clear()

    async def test_fresh_offer_returns_fresh_status(
        self, supabase_client, auth_user, product, supermarket
    ):
        """Active offer with unchanged price → staleness='fresh'."""
        offer = _insert_offer(supabase_client, product, supermarket, _FUTURE_DATE, price=1.29)
        item = _make_item("Latte intero", pinned_offer_id=offer["id"], pinned_product_id=product["id"], pinned_price=1.29)
        shopping_list = _create_list(supabase_client, auth_user, [item])

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/lists/{shopping_list['id']}/deal-freshness")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["staleness"] == "fresh"
        assert data[0]["pinned_offer_id"] == offer["id"]
        assert data[0]["current_price"] == pytest.approx(1.29)

    async def test_expired_offer_returns_expired_status(
        self, supabase_client, auth_user, product, supermarket
    ):
        """Offer with valid_to in the past (is_active=false) → staleness='expired'."""
        offer = _insert_offer(supabase_client, product, supermarket, _PAST_DATE, price=0.99)
        item = _make_item("Latte intero", pinned_offer_id=offer["id"], pinned_product_id=product["id"], pinned_price=0.99)
        shopping_list = _create_list(supabase_client, auth_user, [item])

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/lists/{shopping_list['id']}/deal-freshness")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["staleness"] == "expired"
        assert data[0]["current_price"] == pytest.approx(0.99)

    async def test_price_changed_offer_returns_price_changed_status(
        self, supabase_client, auth_user, product, supermarket
    ):
        """Active offer whose price differs from snapshot → staleness='price_changed'."""
        offer = _insert_offer(supabase_client, product, supermarket, _FUTURE_DATE, price=1.49)
        pinned_price = 1.29  # old snapshot price
        item = _make_item("Latte intero", pinned_offer_id=offer["id"], pinned_product_id=product["id"], pinned_price=pinned_price)
        shopping_list = _create_list(supabase_client, auth_user, [item])

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/lists/{shopping_list['id']}/deal-freshness")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["staleness"] == "price_changed"
        assert data[0]["current_price"] == pytest.approx(1.49)
        assert data[0]["snapshot_price"] == pytest.approx(1.29)

    async def test_missing_offer_returns_unavailable_status(
        self, supabase_client, auth_user, product, supermarket
    ):
        """pinned_offer_id referencing a non-existent offer → staleness='unavailable'."""
        nonexistent_offer_id = str(uuid.uuid4())
        item = _make_item("Latte intero", pinned_offer_id=nonexistent_offer_id, pinned_product_id=product["id"])
        shopping_list = _create_list(supabase_client, auth_user, [item])

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/lists/{shopping_list['id']}/deal-freshness")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["staleness"] == "unavailable"
        assert data[0]["current_price"] is None

    async def test_item_without_pinned_offer_is_omitted(
        self, supabase_client, auth_user, product, supermarket
    ):
        """Items with no pinned_offer_id are not included in freshness results."""
        item = _make_item("Prodotto manuale")  # no pinned_offer_id
        shopping_list = _create_list(supabase_client, auth_user, [item])

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/lists/{shopping_list['id']}/deal-freshness")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_non_member_gets_403(
        self, supabase_client, auth_user, product, supermarket
    ):
        """User who is not a list member gets 403."""
        # Create list owned by auth_user, but override auth to a different user
        item = _make_item("Latte intero")
        shopping_list = _create_list(supabase_client, auth_user, [item])

        other_user_id = str(uuid.uuid4())
        app.dependency_overrides[get_current_user_id] = lambda: other_user_id

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/lists/{shopping_list['id']}/deal-freshness")

        assert resp.status_code == 403

    async def test_hidden_offer_for_viewer_returns_unavailable_without_price(
        self, supabase_client, auth_user, product, supermarket
    ):
        _set_profile_location(
            supabase_client,
            auth_user,
            lat=41.9028,
            lng=12.4964,
            max_distance_km=5,
        )
        (
            supabase_client.table("supermarkets")
            .update({"lat": 45.4642, "lng": 9.19})
            .eq("id", supermarket["id"])
            .execute()
        )
        offer = _insert_offer(supabase_client, product, supermarket, _FUTURE_DATE, price=1.29)
        item = _make_item(
            "Latte intero",
            pinned_offer_id=offer["id"],
            pinned_product_id=product["id"],
            pinned_price=1.29,
        )
        shopping_list = _create_list(supabase_client, auth_user, [item])

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/lists/{shopping_list['id']}/deal-freshness")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["staleness"] == "unavailable"
        assert data[0]["current_price"] is None
        assert data[0]["offer_visibility_status"] == "hidden_for_viewer"
