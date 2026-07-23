"""Unit tests for backend bootstrap helpers."""

from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import APIRouter


@pytest.fixture(autouse=True)
def cleanup_imported_main():
    yield
    sys.modules.pop("api", None)
    sys.modules.pop("api.routers", None)
    sys.modules.pop("main", None)


def _import_main(monkeypatch):
    routers_mod = types.ModuleType("api.routers")
    for name in (
        "admin_products",
        "analytics",
        "auth",
        "contact_requests",
        "favorites",
        "flyers",
        "invite",
        "lists",
        "notifications",
        "ops",
        "offers",
        "optimize",
        "products",
        "purchases",
        "push",
        "supermarkets",
        "users",
    ):
        setattr(routers_mod, name, types.SimpleNamespace(router=APIRouter()))

    monkeypatch.setitem(sys.modules, "api.routers", routers_mod)
    monkeypatch.setitem(
        sys.modules,
        "core.runtime",
        types.SimpleNamespace(ensure_supported_python=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.config",
        types.SimpleNamespace(
            settings=types.SimpleNamespace(
                environment="development",
                frontend_url="http://localhost:3000",
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "services.flyer_cleanup",
        types.SimpleNamespace(
            FlyerCleanupService=lambda: types.SimpleNamespace(run=lambda: None),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "services.extraction_startup_recovery",
        types.SimpleNamespace(
            ExtractionStartupRecoveryService=lambda: types.SimpleNamespace(run=lambda: []),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "services.purchased_items_cleanup",
        types.SimpleNamespace(
            PurchasedItemsCleanupService=lambda: types.SimpleNamespace(run=lambda: None),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "services.notification_jobs",
        types.SimpleNamespace(
            NotificationJobWorker=lambda: types.SimpleNamespace(run_pending=lambda: None),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "apscheduler.schedulers.asyncio",
        types.SimpleNamespace(
            AsyncIOScheduler=lambda: types.SimpleNamespace(
                add_job=lambda *args, **kwargs: None,
                start=lambda: None,
                shutdown=lambda wait=False: None,
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "apscheduler.triggers.cron",
        types.SimpleNamespace(CronTrigger=lambda *args, **kwargs: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "apscheduler.triggers.interval",
        types.SimpleNamespace(IntervalTrigger=lambda *args, **kwargs: None),
    )
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_dev_allow_origins_include_loopback_variants(monkeypatch):
    main = _import_main(monkeypatch)
    monkeypatch.setattr(main, "_dev_extra_origins", lambda frontend_port=3000: [])
    monkeypatch.setattr(
        main,
        "settings",
        types.SimpleNamespace(
            environment="development",
            frontend_url="http://127.0.0.1:3000",
            cors_extra_origins=(
                "https://app.girospesa.local, capacitor://app.girospesa.local"
            ),
        ),
    )

    origins = main._allow_origins()

    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins
    assert "https://app.girospesa.local" in origins
    assert "capacitor://app.girospesa.local" in origins


def test_production_allow_origins_include_frontend_and_capacitor(monkeypatch):
    main = _import_main(monkeypatch)
    monkeypatch.setattr(
        main,
        "settings",
        types.SimpleNamespace(
            environment="production",
            frontend_url="https://app.girospesa.it",
            cors_extra_origins=(
                "https://app.girospesa.local, capacitor://app.girospesa.local"
            ),
        ),
    )

    assert main._allow_origins() == [
        "https://app.girospesa.it",
        "https://app.girospesa.local",
        "capacitor://app.girospesa.local",
    ]


def test_cors_extra_origins_ignore_empty_values(monkeypatch):
    main = _import_main(monkeypatch)
    monkeypatch.setattr(
        main,
        "settings",
        types.SimpleNamespace(
            environment="production",
            frontend_url="https://app.girospesa.it",
            cors_extra_origins=" https://app.girospesa.local, ,",
        ),
    )

    assert main._allow_origins() == [
        "https://app.girospesa.it",
        "https://app.girospesa.local",
    ]
