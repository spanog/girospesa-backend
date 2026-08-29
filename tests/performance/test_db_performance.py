"""Performance tests — Supabase query benchmarks with 10,000 offers.

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

FTS_LIMIT_MS = 500       # name search on 10k offers
FILTER_LIMIT_MS = 500    # active offers filter + JOIN on 10k offers
PAGINATED_LIMIT_MS = 300 # paginated offer list (first page, 50 rows)
DISCOVERY_LIMIT_MS = 500 # first nearby discovery query


class TestDatabaseQueryPerformance:
    """Query timing assertions against a 10,000-offer dataset."""

    def test_fts_query_under_threshold(self, supabase_client, seeded_10k_dataset):
        """Name search on 10k self-contained offers completes in < 500ms."""
        start = time.perf_counter()
        result = (
            supabase_client.table("offers")
            .select("id, name, brand")
            .ilike("name", "%PERF_Prodotto_0%")
            .limit(50)
            .execute()
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < FTS_LIMIT_MS, (
            f"FTS query took {elapsed_ms:.0f}ms — exceeds {FTS_LIMIT_MS}ms threshold. "
            "Check the offer name index."
        )
        assert len(result.data) > 0, "FTS query returned no results — check seeded data"

    def test_active_offers_filter_under_threshold(self, supabase_client, seeded_10k_dataset):
        """Filtering active offers with supermarket data completes in < 500ms."""
        import datetime

        today = datetime.date.today().isoformat()

        start = time.perf_counter()
        result = (
            supabase_client.table("offers")
            .select("id, name, brand, price_offer, valid_to, supermarkets(name)")
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

    def test_offer_count_consistency(self, supabase_client, seeded_10k_dataset):
        """Sanity check: at least 10,000 PERF_ offers exist in the DB."""
        result = (
            supabase_client.table("offers")
            .select("id", count="exact")
            .ilike("name", "PERF_%")
            .execute()
        )
        assert result.count >= 10_000, (
            f"Expected ≥10,000 PERF_ offers but found {result.count}. "
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

    def test_offer_discovery_query_avoids_exact_count(self, supabase_client, seeded_10k_dataset):
        """First nearby offer discovery stays below 500ms without exact count."""
        today = "2026-08-29"
        market_ids = [market["id"] for market in seeded_10k_dataset["supermarkets"]]
        start = time.perf_counter()
        result = (
            supabase_client.table("offers")
            .select("id, name, supermarket_id")
            .in_("supermarket_id", market_ids)
            .eq("is_confirmed", True)
            .eq("offer_kind", "published_target")
            .gte("valid_to", today)
            .order("name")
            .limit(20)
            .execute()
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < DISCOVERY_LIMIT_MS
        assert len(result.data) == 20

    def test_public_flyer_discovery_uses_nearby_branch_index(
        self, supabase_client, perf_supermarkets, perf_public_flyers
    ):
        """Nearby public flyers use the branch-first partial discovery index."""
        market_ids = [market["id"] for market in perf_supermarkets]
        start = time.perf_counter()
        result = (
            supabase_client.table("flyers")
            .select("id, supermarket_id")
            .in_("supermarket_id", market_ids)
            .eq("flyer_kind", "published_target")
            .eq("status", "done")
            .eq("is_public", True)
            .order("created_at", desc=True)
            .limit(1000)
            .execute()
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < DISCOVERY_LIMIT_MS
        assert len(result.data) == len(perf_public_flyers)
