"""Attiva ensure_supabase_local per tutti i test nella cartella integration/."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _require_supabase(ensure_supabase_local):
    """Alias autouse: tutti i test integration richiedono Supabase locale."""
    pass
