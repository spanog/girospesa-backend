"""Integration tests — POST /optimize.

Seeds supermarket, product, offer, auth user, shopping list in the local DB,
then verifies OptimizationResult is correct.

Requires `supabase start` (local Supabase stack).

Run:
    supabase start
    pytest tests/integration/test_optimize.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.optimize import router as optimize_router
from core.auth import get_current_user_id
from tests.conftest import wait_for_user_bootstrap
from tests.snapshot_utils import assert_matches_json_snapshot
app = FastAPI()
app.include_router(optimize_router, prefix="/optimize")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FUTURE_DATE = "2099-12-31"


def _make_list_item(
    name: str,
    *,
    pinned_product_id: str | None = None,
    pinned_offer_id: str | None = None,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": 1,
        "checked": False,
        "purchased": False,
        "added_by": None,
        "added_at": None,
        "pinned_product_id": pinned_product_id,
        "pinned_offer_id": pinned_offer_id,
        "found_deals": [],
    }


def _supabase_with_real_db(supabase_client: object) -> object:
    """Return the service-role client as-is (no storage mock needed for optimize)."""
    return supabase_client


def _create_member_list(supabase_client, auth_user: str, name: str, items: list[dict]) -> dict:
    row = (
        supabase_client.table("shopping_lists")
        .insert({"user_id": auth_user, "name": name, "items": items})
        .execute()
    ).data[0]
    supabase_client.table("list_members").insert({
        "list_id": row["id"],
        "user_id": auth_user,
        "role": "owner",
    }).execute()
    return row


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_user(supabase_client):
    """Create a temporary auth user; yield its UUID; delete after test."""
    email = f"test_optimize_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    wait_for_user_bootstrap(user_id)
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def seeded_supermarket(supabase_client, clean_db):
    """Insert a test supermarket; return its row."""
    slug = f"test-market-{uuid.uuid4().hex[:6]}"
    row = (
        supabase_client.table("supermarkets")
        .insert({
            "name": "Test Market",
            "slug": slug,
            "lat": 45.4654,
            "lng": 9.1859,
        })
        .execute()
    ).data[0]
    return row


@pytest.fixture()
def seeded_product(supabase_client, seeded_supermarket):
    """Insert a canonical product; return its row."""
    row = (
        supabase_client.table("products")
        .insert({
            "name": "Latte intero",
            "brand": "Granarolo",
        })
        .execute()
    ).data[0]
    return row


@pytest.fixture()
def seeded_offer(supabase_client, seeded_product, seeded_supermarket):
    """Insert an active offer for the test product; return its row."""
    row = (
        supabase_client.table("offers")
        .insert({
            "product_id": seeded_product["id"],
            "supermarket_id": seeded_supermarket["id"],
            "supermarket_name": seeded_supermarket["name"],
            "price_offer": 1.29,
            "price_original": 1.59,
            "unit_price": "1,29 €/l",
            "unit_price_value": 1.29,
            "unit_price_unit": "l",
            "valid_to": _FUTURE_DATE,
            "is_confirmed": True,
        })
        .execute()
    ).data[0]
    return row


@pytest.fixture()
def shopping_list_with_match(supabase_client, auth_user, seeded_offer):
    """Create a shopping list with one item that matches the seeded offer."""
    items = [_make_list_item("Latte intero", pinned_product_id=seeded_offer["product_id"])]
    return _create_member_list(supabase_client, auth_user, "Test list", items)


@pytest.fixture()
def shopping_list_empty(supabase_client, auth_user):
    """Create a shopping list with no items."""
    return _create_member_list(supabase_client, auth_user, "Empty list", [])


@pytest.fixture()
def shopping_list_no_match(supabase_client, auth_user, seeded_offer):
    """Create a shopping list whose item has no matching offer in the DB."""
    items = [_make_list_item("Articolo inesistente XYZ 999")]
    return _create_member_list(supabase_client, auth_user, "No-match list", items)


@pytest.fixture()
def shopping_list_pinned_offer_and_no_match(
    supabase_client, auth_user, seeded_product, seeded_offer
):
    """Create a list with one pinned offer item and one manual no-match item."""
    items = [
        _make_list_item(
            "Latte scelto da offerta",
            pinned_product_id=seeded_product["id"],
            pinned_offer_id=seeded_offer["id"],
        ),
        _make_list_item("Articolo inesistente XYZ 999"),
    ]
    return _create_member_list(supabase_client, auth_user, "Mixed list", items)


@pytest.fixture()
def shopping_list_pinned_open_ended_offer(
    supabase_client, auth_user, seeded_product, seeded_offer
):
    """Create a pinned offer list item whose offer has no valid_to date."""
    (
        supabase_client.table("offers")
        .update({"valid_to": None})
        .eq("id", seeded_offer["id"])
        .execute()
    )
    items = [
        _make_list_item(
            "Latte offerta senza scadenza",
            pinned_product_id=seeded_product["id"],
            pinned_offer_id=seeded_offer["id"],
        )
    ]
    return _create_member_list(
        supabase_client, auth_user, "Open-ended offer list", items
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOptimizeIntegration:

    @pytest.fixture(autouse=True)
    def _override_auth(self, auth_user):
        app.dependency_overrides[get_current_user_id] = lambda: auth_user
        yield
        app.dependency_overrides.clear()

    async def test_optimize_returns_coverage_when_offers_match(
        self, supabase_client, shopping_list_with_match, request
    ):
        """Happy path: list has item matching an active offer → coverage_percent > 0."""
        sb = _supabase_with_real_db(supabase_client)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=sb):
                resp = await client.post(
                    "/optimize",
                    json={"list_id": shopping_list_with_match["id"]},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["coverage_percent"] > 0
        assert len(body["store_groups"]) >= 1
        assert body["total_cost"] > 0
        assert body["unmatched_items"] == []
        assert "mode" not in body

        # Verify matched product fields
        matched = body["store_groups"][0]["products"][0]
        assert matched["product_name"] == "Latte intero"
        assert matched["price_offer"] == pytest.approx(1.29)
        assert matched["unit_price_value"] == pytest.approx(1.29)
        assert matched["unit_price_unit"] == "l"
        assert matched["unit_price_label"] == "1,29 €/l"
        assert matched["alternatives"][0]["unit_price_label"] == "1,29 €/l"
        assert matched["offer_id"] != matched["product_id"]  # distinct IDs after fix
        assert_matches_json_snapshot(request, "optimize_with_matching_offer", body)

    async def test_optimize_empty_list_returns_full_coverage(
        self, supabase_client, shopping_list_empty
    ):
        """Empty list → coverage_percent=100, no store groups, no unmatched items."""
        sb = _supabase_with_real_db(supabase_client)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=sb):
                resp = await client.post(
                    "/optimize",
                    json={"list_id": shopping_list_empty["id"]},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["coverage_percent"] == 100
        assert body["store_groups"] == []
        assert body["unmatched_items"] == []
        assert body["total_cost"] == 0
        assert body["total_savings"] == 0

    async def test_optimize_no_matching_offers_returns_zero_coverage(
        self, supabase_client, shopping_list_no_match
    ):
        """Manual items with no offer stay in the plan without alternatives."""
        sb = _supabase_with_real_db(supabase_client)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=sb):
                resp = await client.post(
                    "/optimize",
                    json={"list_id": shopping_list_no_match["id"]},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["coverage_percent"] == 100
        assert body["unmatched_items"] == []
        assert body["store_groups"][0]["supermarket_id"] == "__manual__"
        matched = body["store_groups"][0]["products"][0]
        assert matched["source"] == "manual"
        assert matched["offer_id"] is None
        assert matched["alternatives"] == []

    async def test_optimize_keeps_pinned_offer_when_manual_item_has_no_match(
        self, supabase_client, shopping_list_pinned_offer_and_no_match, seeded_offer
    ):
        """Pinned offer item stays covered while manual no-match item is unmatched."""
        sb = _supabase_with_real_db(supabase_client)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=sb):
                resp = await client.post(
                    "/optimize",
                    json={"list_id": shopping_list_pinned_offer_and_no_match["id"]},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["coverage_percent"] == 100
        assert body["unmatched_items"] == []
        assert len(body["store_groups"]) == 2

        offer_group = next(g for g in body["store_groups"] if g["supermarket_id"] != "__manual__")
        matched = offer_group["products"][0]
        assert matched["offer_id"] == seeded_offer["id"]
        assert matched["match_score"] == pytest.approx(1.0)

    async def test_optimize_keeps_pinned_offer_without_valid_to(
        self, supabase_client, shopping_list_pinned_open_ended_offer, seeded_offer
    ):
        """Pinned active offer with valid_to NULL remains optimizable."""
        sb = _supabase_with_real_db(supabase_client)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=sb):
                resp = await client.post(
                    "/optimize",
                    json={"list_id": shopping_list_pinned_open_ended_offer["id"]},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["coverage_percent"] == 100
        assert body["unmatched_items"] == []
        assert body["store_groups"][0]["products"][0]["offer_id"] == seeded_offer["id"]

    async def test_optimize_ignores_legacy_mode_field(
        self, supabase_client, shopping_list_with_match
    ):
        """Legacy clients may send mode; optimizer ignores it and returns one plan."""
        sb = _supabase_with_real_db(supabase_client)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=sb):
                resp = await client.post(
                    "/optimize",
                    json={
                        "list_id": shopping_list_with_match["id"],
                        "mode": "minimize_stores",
                    },
                )

        assert resp.status_code == 200
        body = resp.json()
        assert "mode" not in body
        assert body["coverage_percent"] > 0

    async def test_optimize_manual_item_uses_fuzzy_product_name_search(
        self, supabase_client, auth_user, seeded_product, seeded_offer
    ):
        """Manual item stays manual while fuzzy offers appear as alternatives."""
        items = [_make_list_item("Late intero")]
        row = _create_member_list(supabase_client, auth_user, "Fuzzy list", items)
        sb = _supabase_with_real_db(supabase_client)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=sb):
                resp = await client.post("/optimize", json={"list_id": row["id"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["coverage_percent"] == 100
        matched = body["store_groups"][0]["products"][0]
        assert matched["source"] == "manual"
        assert matched["product_name"] == "Late intero"
        assert matched["product_id"] is None
        assert matched["offer_id"] is None
        assert matched["alternatives"][0]["product_id"] == seeded_product["id"]
        assert matched["alternatives"][0]["offer_id"] == seeded_offer["id"]
