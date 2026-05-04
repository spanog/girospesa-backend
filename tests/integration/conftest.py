"""Avvia stack Docker isolato per tutti i test nella cartella integration/."""

import pytest

from scripts.integration_stack import apply_integration_env, run_compose

apply_integration_env()


@pytest.fixture(scope="session", autouse=True)
def ensure_integration_stack():
    """Avvia e distrugge solo stack Docker dedicato ai test integration."""
    apply_integration_env()
    try:
        run_compose("up", "-d", "--wait")
    except Exception as exc:
        pytest.exit(f"Stack integration Docker non avviato: {exc}", returncode=1)
    yield
    run_compose("down", "-v", "--remove-orphans")


@pytest.fixture(scope="session", autouse=True)
def _require_supabase(ensure_integration_stack, ensure_supabase_local):
    """Alias autouse: tutti i test integration richiedono Supabase test."""
    pass
