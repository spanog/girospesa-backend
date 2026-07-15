"""Fixtures for performance tests — require Supabase local, seed large dataset once."""

from __future__ import annotations

import os
import uuid
import pytest

import core.config as core_config
from core.config import Settings
from scripts.integration_stack import integration_env, run_compose


# ---------------------------------------------------------------------------
# Guard: tutti i performance test richiedono Supabase locale
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _skip_unless_enabled():
    """Performance benchmarks are opt-in; normal pytest runs stay deterministic."""
    if os.environ.get("RUN_PERFORMANCE_TESTS") != "1":
        pytest.skip("Set RUN_PERFORMANCE_TESTS=1 to run performance benchmarks.")


@pytest.fixture(scope="session", autouse=True)
def performance_test_env(_skip_unless_enabled):
    """Apply isolated integration-stack env only for performance sessions."""
    monkeypatch = pytest.MonkeyPatch()
    for key, value in integration_env().items():
        monkeypatch.setenv(key, value)
    refreshed = Settings()  # type: ignore[call-arg]
    for field, value in refreshed.model_dump().items():
        setattr(core_config.settings, field, value)
    yield
    monkeypatch.undo()
    restored = Settings()  # type: ignore[call-arg]
    for field, value in restored.model_dump().items():
        setattr(core_config.settings, field, value)


@pytest.fixture(scope="session", autouse=True)
def ensure_performance_stack(performance_test_env):
    """Start and destroy the isolated Docker stack for performance tests."""
    try:
        run_compose("up", "-d", "--wait")
    except Exception as exc:
        pytest.exit(f"Stack performance Docker non avviato: {exc}", returncode=1)
    yield
    run_compose("down", "-v", "--remove-orphans")


@pytest.fixture(scope="session", autouse=True)
def _require_supabase(ensure_performance_stack, ensure_supabase_local):
    """All performance tests require reachable Supabase test services."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FUTURE_DATE = "2099-12-31"
_PERF_PREFIX = "PERF_"  # namespace to avoid collisions with other test data
_NIL_UUID = "00000000-0000-0000-0000-000000000000"

TRUNCATE_TABLES = [
    "list_members",
    "shopping_lists",
    "offers",
    "products",
    "flyers",
    "supermarkets",
]


def _delete_perf_data(supabase_client) -> None:
    """Remove only data seeded by performance tests (PERF_ prefix in name)."""
    for table in TRUNCATE_TABLES:
        try:
            supabase_client.table(table).delete().neq("id", _NIL_UUID).execute()
        except Exception:
            pass


def _batch_insert(supabase_client, table: str, rows: list[dict], batch_size: int = 1000) -> list[dict]:
    """Insert rows in batches; return all inserted rows."""
    inserted: list[dict] = []
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        result = supabase_client.table(table).insert(batch).execute()
        inserted.extend(result.data)
    return inserted


def _product_row(name: str, brand: str, grams: int) -> dict:
    return {
        "name": name,
        "brand": brand,
    }


# ---------------------------------------------------------------------------
# Session-scoped supermarkets (shared by all performance tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def perf_supermarkets(supabase_client):
    """Seed 5 test supermarkets; clean up at session end."""
    markets = [
        {"name": f"{_PERF_PREFIX}Market_{i}", "slug": f"perf-market-{uuid.uuid4().hex[:6]}", "lat": 45.46 + i * 0.01, "lng": 9.18}
        for i in range(5)
    ]
    rows = _batch_insert(supabase_client, "supermarkets", markets)
    yield rows
    _delete_perf_data(supabase_client)


# ---------------------------------------------------------------------------
# Session-scoped 10k products + 10k offers (for DB performance tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def seeded_10k_dataset(supabase_client, perf_supermarkets):
    """Seed 10,000 products and 10,000 offers; return (products, offers, supermarkets)."""
    # --- Products ---
    products_payload = [
        _product_row(
            name=f"{_PERF_PREFIX}Prodotto_{i:05d}",
            brand=f"Brand_{i % 10:02d}",
            grams=(i % 6 + 1) * 100,
        )
        for i in range(10_000)
    ]
    products = _batch_insert(supabase_client, "products", products_payload)

    # --- Offers (1 per product, cycling through 5 supermarkets) ---
    offers_payload = [
        {
            "product_id": p["id"],
            "supermarket_id": perf_supermarkets[idx % 5]["id"],
            "supermarket_name": perf_supermarkets[idx % 5]["name"],
            "price_offer": round(0.99 + (idx % 100) * 0.1, 2),
            "price_original": round(1.29 + (idx % 100) * 0.1, 2),
            "valid_to": _FUTURE_DATE,
        }
        for idx, p in enumerate(products)
    ]
    offers = _batch_insert(supabase_client, "offers", offers_payload)

    yield {"products": products, "offers": offers, "supermarkets": perf_supermarkets}


# ---------------------------------------------------------------------------
# Session-scoped 1000 products + 1000 offers (for optimizer performance tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def seeded_1k_optimizer_dataset(supabase_client):
    """Seed 1,000 products and 1,000 offers for optimizer benchmarking."""
    _delete_perf_data(supabase_client)
    markets = [
        {"name": f"{_PERF_PREFIX}OptMarket_{i}", "slug": f"perf-opt-market-{uuid.uuid4().hex[:6]}", "lat": 45.46 + i * 0.01, "lng": 9.18}
        for i in range(5)
    ]
    supermarkets = _batch_insert(supabase_client, "supermarkets", markets)
    product_names = [
        "latte", "burro", "pane", "pasta", "riso", "olio", "pollo", "manzo",
        "pesce", "uova", "formaggio", "yogurt", "mozzarella", "prosciutto", "salame",
        "carote", "patate", "cipolle", "pomodori", "zucchine",
    ]
    products_payload = [
        _product_row(
            name=f"{_PERF_PREFIX}{product_names[i % len(product_names)].capitalize()} Opt_{i:04d}",
            brand=f"BrandOpt_{i % 5:02d}",
            grams=(i % 4 + 1) * 250,
        )
        for i in range(1_000)
    ]
    products = _batch_insert(supabase_client, "products", products_payload)

    offers_payload = [
        {
            "product_id": p["id"],
            "supermarket_id": supermarkets[idx % 5]["id"],
            "supermarket_name": supermarkets[idx % 5]["name"],
            "price_offer": round(1.0 + (idx % 50) * 0.05, 2),
            "price_original": round(1.5 + (idx % 50) * 0.05, 2),
            "valid_to": _FUTURE_DATE,
        }
        for idx, p in enumerate(products)
    ]
    offers = _batch_insert(supabase_client, "offers", offers_payload)

    yield {"products": products, "offers": offers, "supermarkets": supermarkets}
    _delete_perf_data(supabase_client)
