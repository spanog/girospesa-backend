from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_config_mod.settings = types.SimpleNamespace(ops_cron_secret="test-ops-secret")
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

import api.routers.ops as _ops_module
from api.routers.ops import router as _ops_router

_test_app = FastAPI()
_test_app.include_router(_ops_router, prefix="/ops")


async def _post(secret: str | None = None) -> httpx.Response:
    headers = {}
    if secret is not None:
        headers["x-ops-secret"] = secret
    transport = httpx.ASGITransport(app=_test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/ops/cron/daily-maintenance", headers=headers)


async def _get_probe(secret: str | None = None) -> httpx.Response:
    headers = {}
    if secret is not None:
        headers["x-ops-secret"] = secret
    transport = httpx.ASGITransport(app=_test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/ops/smtp-probe", headers=headers)


@pytest.mark.asyncio
async def test_daily_maintenance_requires_matching_secret():
    response = await _post("wrong-secret")

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid ops secret"


@pytest.mark.asyncio
async def test_daily_maintenance_rejects_when_secret_not_configured():
    with patch.object(_ops_module.settings, "ops_cron_secret", ""):
        response = await _post("test-ops-secret")

    assert response.status_code == 503
    assert response.json()["detail"] == "Ops cron secret is not configured"


@pytest.mark.asyncio
async def test_daily_maintenance_runs_both_cleanup_services():
    flyer_cleanup = MagicMock(return_value=7)
    purchased_cleanup = MagicMock(return_value=3)

    with (
        patch.object(_ops_module.FlyerCleanupService, "run", flyer_cleanup),
        patch.object(_ops_module.PurchasedItemsCleanupService, "run", purchased_cleanup),
    ):
        response = await _post("test-ops-secret")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "deleted_offers": 7,
        "removed_purchased_items": 3,
    }
    flyer_cleanup.assert_called_once_with()
    purchased_cleanup.assert_called_once_with()


@pytest.mark.asyncio
async def test_smtp_probe_requires_matching_secret():
    response = await _get_probe("wrong-secret")

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid ops secret"


@pytest.mark.asyncio
async def test_smtp_probe_returns_service_payload():
    payload = _ops_module.SmtpProbeResponse(
        status="ok",
        host="smtps.aruba.it",
        port=465,
        timeout_seconds=10,
        ssl_mode=True,
        tls_mode=False,
        stage="ehlo",
        resolved_addresses=["62.149.128.200"],
        connect_duration_ms=123,
        ehlo_code=250,
        ehlo_message="ok",
        tls_established=True,
        tls_cipher="TLS_AES_256_GCM_SHA384",
        error_type=None,
        error_message=None,
    )

    with patch.object(_ops_module.SmtpProbeService, "run", return_value=payload):
        response = await _get_probe("test-ops-secret")

    assert response.status_code == 200
    assert response.json()["host"] == "smtps.aruba.it"
    assert response.json()["tls_established"] is True


@pytest.mark.asyncio
async def test_smtp_probe_maps_configuration_errors_to_503():
    with patch.object(
        _ops_module.SmtpProbeService,
        "run",
        side_effect=_ops_module.ContactRequestConfigurationError("Missing contact mail configuration: smtp_host"),
    ):
        response = await _get_probe("test-ops-secret")

    assert response.status_code == 503
    assert response.json()["detail"] == "Missing contact mail configuration: smtp_host"
