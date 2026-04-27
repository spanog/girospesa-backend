"""Performance tests — Supabase query benchmarks with 10,000+ products.

Verifies that indexed queries (FTS, is_active filter, JOIN) complete within
acceptable wall-clock thresholds. A failure here indicates a missing index
or a regression in query planning.

Requires `supabase start` (local Supabase stack).

Run:
    supabase start
    pytest tests/performance/test_db_performance.py -v -s
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# Thresholds (ms)
# ---------------------------------------------------------------------------

FTS_LIMIT_MS = 500       # full-text search on 10k products
FILTER_LIMIT_MS = 500    # active offers filter + JOIN on 10k offers
PAGINATED_LIMIT_MS = 300 # paginated offer list (first page, 50 rows)


class TestDatabaseQueryPerformance:
    """Query timing assertions against a 10,000-product dataset."""

    def test_fts_query_under_threshold(self, supabase_client, seeded_10k_dataset):
        """FTS search on `name_tsv` with 10k products completes in < 500ms.

        Uses the GIN-indexed `name_tsv` column. Slow result indicates missing
        index or PostgREST/Supabase config issue.
        """
        start = time.perf_counter()
        result = (
            supabase_client.table("products")
            .select("id, name, brand, format")
            .ilike("name", "%PERF_Prodotto_0%")
            .limit(50)
            .execute()
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < FTS_LIMIT_MS, (
            f"FTS query took {elapsed_ms:.0f}ms — exceeds {FTS_LIMIT_MS}ms threshold. "
            "Check GIN index on name_tsv."
        )
        assert len(result.data) > 0, "FTS query returned no results — check seeded data"

    def test_active_offers_filter_under_threshold(self, supabase_client, seeded_10k_dataset):
        """Filtering active offers (valid_to >= today) with product+supermarket JOIN completes in < 500ms.

        The `valid_to` column should have a B-tree index. The JOIN with `products`
        and `supermarkets` should use FK-based lookups.
        """
        import datetime

        today = datetime.date.today().isoformat()

        start = time.perf_counter()
        result = (
            supabase_client.table("offers")
            .select("id, price_offer, valid_to, products(name, brand), supermarkets(name)")
            .gte("valid_to", today)
            .limit(50)
            .execute()
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < FILTER_LIMIT_MS, (
            f"Active offers query took {elapsed_ms:.0f}ms — exceeds {FILTER_LIMIT_MS}ms threshold. "
            "Check index on offers.valid_to."
        )
        assert len(result.data) > 0, "Active offers query returned no results — check seeded data"

    def test_paginated_offers_first_page_under_threshold(self, supabase_client, seeded_10k_dataset):
        """Paginated offer list (page 1, 50 rows, ordered by created_at desc) completes in < 300ms."""
        start = time.perf_counter()
        result = (
            supabase_client.table("offers")
            .select("id, price_offer, price_original, discount_pct, valid_to, supermarket_name")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < PAGINATED_LIMIT_MS, (
            f"Paginated query took {elapsed_ms:.0f}ms — exceeds {PAGINATED_LIMIT_MS}ms threshold. "
            "Check index on offers.created_at."
        )
        assert len(result.data) == 50

    def test_product_count_consistency(self, supabase_client, seeded_10k_dataset):
        """Sanity check: at least 10,000 PERF_ products exist in the DB."""
        result = (
            supabase_client.table("products")
            .select("id", count="exact")
            .ilike("name", "PERF_%")
            .execute()
        )
        assert result.count >= 10_000, (
            f"Expected ≥10,000 PERF_ products but found {result.count}. "
            "Seeding may have failed — check conftest.py."
        )

    def test_supermarket_filter_combined_with_active_offers(self, supabase_client, seeded_10k_dataset):
        """Filtering active offers by supermarket_id completes in < 300ms."""
        import datetime

        today = datetime.date.today().isoformat()
        market_id = seeded_10k_dataset["supermarkets"][0]["id"]

        start = time.perf_counter()
        result = (
            supabase_client.table("offers")
            .select("id, price_offer, supermarket_name")
            .gte("valid_to", today)
            .eq("supermarket_id", market_id)
            .limit(50)
            .execute()
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < PAGINATED_LIMIT_MS, (
            f"Supermarket-filtered query took {elapsed_ms:.0f}ms — exceeds {PAGINATED_LIMIT_MS}ms."
        )
        assert len(result.data) > 0
