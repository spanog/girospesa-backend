"""Performance tests — optimizer with 1000 products and 50-item shopping list.

Verifies that the greedy set-cover algorithm (POST /optimize) completes within
2 seconds when the product catalog has 1,000 active offers and the shopping
list has 50 unchecked items.

Requires `supabase start` (local Supabase stack).

Run:
    supabase start
    pytest tests/performance/test_optimizer_performance.py -v -s
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.optimize import router as optimize_router
from core.auth import get_current_user_id

app = FastAPI()
app.include_router(optimize_router, prefix="/optimize")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

OPTIMIZE_LIMIT_S = 2.0   # end-to-end wall time for POST /optimize

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_list_item(name: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "checked": False,
        "added_by": None,
        "added_at": None,
        "pinned_product_id": None,
        "pinned_offer_id": None,
        "found_deals": [],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_auth_user(supabase_client):
    """Temporary auth user for optimizer performance tests."""
    email = f"perf_opt_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def shopping_list_50_items(supabase_client, seeded_1k_optimizer_dataset, perf_auth_user):
    """Create a shopping list with 50 unchecked items matching products in the dataset.

    Item names are chosen to match the seeded product names (fuzzy match ≥ 0.5).
    """
    product_names = [
        "latte", "burro", "pane", "pasta", "riso", "olio", "pollo", "manzo",
        "pesce", "uova", "formaggio", "yogurt", "mozzarella", "prosciutto", "salame",
        "carote", "patate", "cipolle", "pomodori", "zucchine",
    ]
    items = [_make_list_item(f"{product_names[i % len(product_names)].capitalize()} {i}") for i in range(50)]
    row = (
        supabase_client.table("shopping_lists")
        .insert({"user_id": perf_auth_user, "name": "Perf list 50 items", "items": items})
        .execute()
    ).data[0]
    yield row
    supabase_client.table("shopping_lists").delete().eq("id", row["id"]).execute()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOptimizerPerformance:

    @pytest.fixture(autouse=True)
    def _override_auth(self, perf_auth_user):
        app.dependency_overrides[get_current_user_id] = lambda: perf_auth_user
        yield
        app.dependency_overrides.clear()

    async def test_optimize_50_items_1000_products_under_2s(
        self, supabase_client, seeded_1k_optimizer_dataset, shopping_list_50_items
    ):
        """POST /optimize with 50 items and 1,000 active offers completes in < 2s.

        Measures wall-clock time from HTTP request dispatch to response received.
        The greedy set-cover algorithm is O(items × offers × stores); at this
        scale it must stay within the 2-second budget.
        """
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=supabase_client):
                start = time.perf_counter()
                resp = await client.post(
                    "/optimize",
                    json={"list_id": shopping_list_50_items["id"]},
                )
                elapsed_s = time.perf_counter() - start

        assert resp.status_code == 200, f"Unexpected status: {resp.status_code} — {resp.text}"
        body = resp.json()

        assert elapsed_s < OPTIMIZE_LIMIT_S, (
            f"Optimizer took {elapsed_s:.2f}s with 50 items / 1k products — "
            f"exceeds {OPTIMIZE_LIMIT_S}s threshold. "
            "Check difflib loop or DB query performance."
        )

        assert body["coverage_percent"] > 0, "Expected at least some items covered"
        assert len(body["store_groups"]) >= 1, "Expected at least one store group"

    async def test_optimize_legacy_mode_payload_under_2s(
        self, supabase_client, seeded_1k_optimizer_dataset, shopping_list_50_items
    ):
        """Legacy mode payload is ignored and still completes in < 2s."""
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.optimize.get_supabase", return_value=supabase_client):
                start = time.perf_counter()
                resp = await client.post(
                    "/optimize",
                    json={"list_id": shopping_list_50_items["id"], "mode": "minimize_stores"},
                )
                elapsed_s = time.perf_counter() - start

        assert resp.status_code == 200
        assert elapsed_s < OPTIMIZE_LIMIT_S, (
            f"Optimize with legacy mode took {elapsed_s:.2f}s — exceeds {OPTIMIZE_LIMIT_S}s."
        )
        assert "mode" not in resp.json()
