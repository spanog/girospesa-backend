"""Integration tests — favorites full lifecycle.

Scenarios covered:
1. Favorite stable on offer expiry: valid_to=yesterday → favorites row intact, product_id unchanged.
2. Favorite stable on flyer deletion: DELETE flyers row → offers.flyer_id becomes NULL, favorites intact.
3. New offer reflected in GET /favorites: INSERT new offer for favorited product → returned without touching favorites.
4. No active offer: no offer with is_active=true → has_active_offer=False.
5. POST /favorites FK constraint: product_id NOT NULL enforced by API (422 on missing field).

Requires `supabase start` (local Supabase stack).

Run:
    supabase start
    pytest tests/integration/test_favorites_lifecycle.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.favorites import router as favorites_router
from core.auth import get_current_user_id

app = FastAPI()
app.include_router(favorites_router, prefix="/favorites")

_PAST_DATE = "2000-01-01"
_FUTURE_DATE = "2099-12-31"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_user(supabase_client):
    email = f"test_fav_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def supermarket(supabase_client, clean_db):
    row = (
        supabase_client.table("supermarkets")
        .insert(
            {
                "name": "Test Market",
                "slug": f"tm-{uuid.uuid4().hex[:6]}",
                "lat": 45.0,
                "lng": 9.0,
            }
        )
        .execute()
    ).data[0]
    return row


@pytest.fixture()
def product(supabase_client, supermarket):
    row = (
        supabase_client.table("products")
        .insert({"name": "Pasta Barilla", "brand": "Barilla", "format": "500g"})
        .execute()
    ).data[0]
    return row


@pytest.fixture()
def flyer(supabase_client, supermarket):
    row = (
        supabase_client.table("flyers")
        .insert(
            {
                "supermarket_id": supermarket["id"],
                "supermarket_name": supermarket["name"],
                "valid_from": "2099-01-01",
                "valid_to": _FUTURE_DATE,
                "status": "done",
                "file_url": "https://example.com/test.pdf",
                "file_type": "pdf",
            }
        )
        .execute()
    ).data[0]
    return row


def _insert_offer(
    supabase_client,
    product: dict,
    supermarket: dict,
    valid_to: str,
    price: float = 0.99,
    flyer_id: str | None = None,
) -> dict:
    payload: dict = {
        "product_id": product["id"],
        "supermarket_id": supermarket["id"],
        "supermarket_name": supermarket["name"],
        "price_offer": price,
        "price_original": 1.49,
        "valid_to": valid_to,
    }
    if flyer_id:
        payload["flyer_id"] = flyer_id
    return (
        supabase_client.table("offers").insert(payload).execute()
    ).data[0]


def _insert_favorite(supabase_client, user_id: str, product_id: str) -> dict:
    return (
        supabase_client.table("favorites")
        .insert({"user_id": user_id, "product_id": product_id})
        .execute()
    ).data[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFavoritesLifecycle:

    @pytest.fixture(autouse=True)
    def _override_auth(self, auth_user):
        app.dependency_overrides[get_current_user_id] = lambda: auth_user
        yield
        app.dependency_overrides.clear()

    async def test_favorite_stable_on_offer_expiry(
        self, supabase_client, auth_user, product, supermarket
    ):
        """When an offer expires (valid_to=yesterday, is_active becomes false),
        the favorites row must remain intact with the same product_id."""
        offer = _insert_offer(supabase_client, product, supermarket, _PAST_DATE)
        fav = _insert_favorite(supabase_client, auth_user, product["id"])

        # Verify offer is now inactive
        offer_row = (
            supabase_client.table("offers")
            .select("id, is_active")
            .eq("id", offer["id"])
            .single()
            .execute()
        ).data
        assert offer_row["is_active"] is False

        # Favorites row must still exist with the same product_id
        fav_row = (
            supabase_client.table("favorites")
            .select("id, product_id")
            .eq("id", fav["id"])
            .single()
            .execute()
        ).data
        assert fav_row is not None
        assert fav_row["product_id"] == product["id"]

        # GET /favorites must show no active offer but the product is still there
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.favorites.get_supabase", return_value=supabase_client):
                resp = await client.get("/favorites")

        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["product_id"] == product["id"]
        assert items[0]["has_active_offer"] is False
        assert items[0]["active_offer"] is None

    async def test_favorite_stable_on_flyer_deletion(
        self, supabase_client, auth_user, product, supermarket, flyer
    ):
        """When the flyer row is deleted, offers.flyer_id becomes NULL (ON DELETE SET NULL).
        The favorites row must remain intact."""
        offer = _insert_offer(
            supabase_client, product, supermarket, _FUTURE_DATE, flyer_id=flyer["id"]
        )
        fav = _insert_favorite(supabase_client, auth_user, product["id"])

        # Delete the flyer
        supabase_client.table("flyers").delete().eq("id", flyer["id"]).execute()

        # offers.flyer_id must be NULL (ON DELETE SET NULL)
        offer_row = (
            supabase_client.table("offers")
            .select("id, flyer_id, is_active")
            .eq("id", offer["id"])
            .single()
            .execute()
        ).data
        assert offer_row is not None, "Offer row must still exist after flyer deletion"
        assert offer_row["flyer_id"] is None, "flyer_id must be NULL after ON DELETE SET NULL"

        # Favorites row must still exist unchanged
        fav_row = (
            supabase_client.table("favorites")
            .select("id, product_id")
            .eq("id", fav["id"])
            .single()
            .execute()
        ).data
        assert fav_row is not None
        assert fav_row["product_id"] == product["id"]

    async def test_new_offer_reflected_in_get_favorites(
        self, supabase_client, auth_user, product, supermarket
    ):
        """When a new offer is inserted for a favorited product,
        GET /favorites returns the updated offer without touching the favorites row."""
        # Seed an expired offer first, then insert a new active one
        _insert_offer(supabase_client, product, supermarket, _PAST_DATE, price=1.20)
        fav = _insert_favorite(supabase_client, auth_user, product["id"])
        new_offer = _insert_offer(
            supabase_client, product, supermarket, _FUTURE_DATE, price=0.89
        )

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.favorites.get_supabase", return_value=supabase_client):
                resp = await client.get("/favorites")

        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["product_id"] == product["id"]
        assert items[0]["has_active_offer"] is True
        assert items[0]["active_offer"]["id"] == new_offer["id"]
        assert items[0]["active_offer"]["price_offer"] == pytest.approx(0.89)

        # Verify the favorites row itself was never touched
        fav_row = (
            supabase_client.table("favorites")
            .select("id, product_id")
            .eq("id", fav["id"])
            .single()
            .execute()
        ).data
        assert fav_row["product_id"] == product["id"]

    async def test_no_active_offer_returns_no_offer_status(
        self, supabase_client, auth_user, product, supermarket
    ):
        """When no offer with is_active=true exists for the favorited product,
        GET /favorites returns has_active_offer=False and active_offer=None."""
        _insert_offer(supabase_client, product, supermarket, _PAST_DATE)
        _insert_favorite(supabase_client, auth_user, product["id"])

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.favorites.get_supabase", return_value=supabase_client):
                resp = await client.get("/favorites")

        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["has_active_offer"] is False
        assert items[0]["active_offer"] is None

    async def test_post_favorites_requires_product_id(self, supabase_client, auth_user):
        """POST /favorites without product_id must return 422 (validation error)."""
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.favorites.get_supabase", return_value=supabase_client):
                resp = await client.post("/favorites", json={})

        assert resp.status_code == 422

    async def test_post_favorites_with_null_product_id_rejected(
        self, supabase_client, auth_user
    ):
        """POST /favorites with product_id=None must return 422 (NOT NULL constraint)."""
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.favorites.get_supabase", return_value=supabase_client):
                resp = await client.post("/favorites", json={"product_id": None})

        assert resp.status_code == 422
