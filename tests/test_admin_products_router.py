from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_db_mod = types.ModuleType("core.database")
_db_mod.get_supabase = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.database"] = _db_mod

_auth_mod = types.ModuleType("core.auth")
_auth_mod.require_admin = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.auth"] = _auth_mod

_config_mod = types.ModuleType("core.config")
_config_mod.settings = MagicMock()  # type: ignore[attr-defined]
sys.modules["core.config"] = _config_mod

from fastapi import FastAPI
import httpx
import pytest

import api.routers.admin_products as _admin_products_module
from api.routers.admin_products import router

_DEP_REQUIRE_ADMIN = _admin_products_module.require_admin

test_app = FastAPI()
test_app.include_router(router, prefix="/admin/products")


def _admin_dep():
    return {"id": "admin-1", "app_metadata": {"role": "admin"}}


async def _get(url: str) -> httpx.Response:
    test_app.dependency_overrides = {_DEP_REQUIRE_ADMIN: _admin_dep}
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


@pytest.mark.asyncio
async def test_list_products_applies_category_and_subcategory_filters():
    sb = MagicMock()
    select_query = sb.table.return_value.select.return_value
    range_query = select_query.eq.return_value.order.return_value.range.return_value
    range_query.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _get(
            "/admin/products?category=dispensa&subcategory=Primi%20Piatti%20e%20Preparati"
        )

    assert resp.status_code == 200
    select_query.eq.assert_any_call("is_archived", False)
    range_query.eq.assert_any_call("category", "dispensa")
    range_query.eq.return_value.eq.assert_any_call(
        "subcategory", "Primi Piatti e Preparati"
    )


@pytest.mark.asyncio
async def test_list_products_without_subcategory_keeps_current_behavior():
    sb = MagicMock()
    select_query = sb.table.return_value.select.return_value
    range_query = select_query.eq.return_value.order.return_value.range.return_value
    range_query.eq.return_value.execute.return_value = MagicMock(data=[])

    with patch("api.routers.admin_products.get_supabase", return_value=sb):
        resp = await _get("/admin/products?category=dispensa")

    assert resp.status_code == 200
    select_query.eq.assert_any_call("is_archived", False)
    range_query.eq.assert_any_call("category", "dispensa")
    assert ("subcategory", "Primi Piatti e Preparati") not in [
        call.args for call in range_query.eq.return_value.eq.call_args_list
    ]
