from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, AsyncMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

for _mod in ("supabase", "jose", "jose.jwt", "geopy", "geopy.geocoders"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_config_mod = types.ModuleType("core.config")
_config_mod.settings = MagicMock()
sys.modules["core.config"] = _config_mod
sys.modules["core.database"] = MagicMock()

_auth_mod = types.ModuleType("core.auth")
_auth_mod.get_current_user_id = MagicMock()
sys.modules["core.auth"] = _auth_mod

from fastapi import FastAPI
import httpx
import pytest

import api.routers.lists as _lists_module
from api.routers.lists import router as _lists_router

_DEP_GET_USER_ID = _lists_module.get_current_user_id

_test_app = FastAPI()
_test_app.include_router(_lists_router, prefix="/lists")


def _deps(user_id: str = "user-1") -> dict:
    return {_DEP_GET_USER_ID: lambda: user_id}


def _make_sb(items: list[dict]) -> MagicMock:
    sb = MagicMock()
    shopping_lists_table = MagicMock()
    purchase_history_table = MagicMock()

    shopping_lists_table.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
        data={"items": items}
    )
    purchase_history_table.delete.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    sb.table.side_effect = lambda name: {
        "shopping_lists": shopping_lists_table,
        "purchase_history": purchase_history_table,
    }[name]
    return sb


@pytest.mark.asyncio
async def test_remove_purchased_item_deletes_purchase_history():
    _test_app.dependency_overrides = _deps("user-1")
    transport = httpx.ASGITransport(app=_test_app)

    items = [
        {
            "id": "item-1",
            "name": "Latte",
            "quantity": 1,
            "purchased": True,
            "purchased_by": "buyer-42",
            "purchased_at": "2026-05-19T10:00:00+00:00",
        }
    ]
    sb = _make_sb(items)
    purchase_history_table = sb.table("purchase_history")

    with patch.object(_lists_module, "get_supabase", return_value=sb), \
         patch.object(_lists_module, "_verify_member", return_value=None), \
         patch.object(_lists_module, "_rpc_remove_list_item", new=AsyncMock(return_value=None)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/lists/list-1/items/item-1")

    assert resp.status_code == 204
    purchase_history_table.delete.assert_called_once()
    purchase_history_table.delete.return_value.eq.assert_called_once_with("list_item_id", "item-1")
    purchase_history_table.delete.return_value.eq.return_value.eq.assert_called_once_with("user_id", "buyer-42")


@pytest.mark.asyncio
async def test_remove_unpurchased_item_skips_purchase_history():
    _test_app.dependency_overrides = _deps("user-1")
    transport = httpx.ASGITransport(app=_test_app)

    items = [
        {
            "id": "item-1",
            "name": "Pane",
            "quantity": 1,
            "purchased": False,
            "purchased_by": None,
            "purchased_at": None,
        }
    ]
    sb = _make_sb(items)
    purchase_history_table = sb.table("purchase_history")

    with patch.object(_lists_module, "get_supabase", return_value=sb), \
         patch.object(_lists_module, "_verify_member", return_value=None), \
         patch.object(_lists_module, "_rpc_remove_list_item", new=AsyncMock(return_value=None)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/lists/list-1/items/item-1")

    assert resp.status_code == 204
    purchase_history_table.delete.assert_not_called()


@pytest.mark.asyncio
async def test_remove_item_not_found_skips_purchase_history():
    """Item absent from list — no purchase_history delete attempted."""
    _test_app.dependency_overrides = _deps("user-1")
    transport = httpx.ASGITransport(app=_test_app)

    sb = _make_sb([])  # empty list
    purchase_history_table = sb.table("purchase_history")

    with patch.object(_lists_module, "get_supabase", return_value=sb), \
         patch.object(_lists_module, "_verify_member", return_value=None), \
         patch.object(_lists_module, "_rpc_remove_list_item", new=AsyncMock(return_value=None)):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/lists/list-1/items/ghost-item")

    assert resp.status_code == 204
    purchase_history_table.delete.assert_not_called()
