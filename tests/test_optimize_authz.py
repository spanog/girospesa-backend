from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_config_mod.settings = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

_auth_mod = types.ModuleType("core.auth")
_auth_mod.get_current_user_id = MagicMock()
sys.modules["core.auth"] = _auth_mod

from fastapi import FastAPI, HTTPException
import httpx
import pytest

import api.routers.optimize as _optimize_module
from api.routers.optimize import router as _optimize_router

_DEP_GET_USER_ID = _optimize_module.get_current_user_id

_test_app = FastAPI()
_test_app.include_router(_optimize_router, prefix="/optimize")


@pytest.mark.asyncio
async def test_optimize_403_non_member():
    _test_app.dependency_overrides = {_DEP_GET_USER_ID: lambda: "user-1"}
    transport = httpx.ASGITransport(app=_test_app)
    with patch.object(_optimize_module, "get_supabase", return_value=MagicMock()), \
         patch.object(_optimize_module, "_verify_member", side_effect=HTTPException(status_code=403, detail="Not a member"), create=True):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/optimize",
                json={"list_id": "list-1", "mode": "maximize_savings"},
            )

    assert resp.status_code == 403
