"""Avvia stack Docker isolato per tutti i test nella cartella integration/."""

import pytest

import core.config as core_config
from core.config import Settings
from scripts.integration_stack import integration_env, run_compose


def _reload_runtime_settings() -> None:
    refreshed = Settings()  # type: ignore[call-arg]
    for field, value in refreshed.model_dump().items():
        setattr(core_config.settings, field, value)


@pytest.fixture(scope="session", autouse=True)
def integration_test_env():
    """Applica env integration solo dentro sessione pytest e poi ripristina."""
    monkeypatch = pytest.MonkeyPatch()
    for key, value in integration_env().items():
        monkeypatch.setenv(key, value)
    _reload_runtime_settings()
    yield
    monkeypatch.undo()
    _reload_runtime_settings()


@pytest.fixture(scope="session", autouse=True)
def ensure_integration_stack(integration_test_env):
    """Avvia e distrugge solo stack Docker dedicato ai test integration."""
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
