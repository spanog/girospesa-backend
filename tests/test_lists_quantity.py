import pytest
from api.routers.lists import _patch_item_in_items, _patch_quantity_in_items


def _make_item(item_id: str, quantity: float = 1.0) -> dict:
    return {
        "id": item_id,
        "name": "Test",
        "quantity": quantity,
        "checked": False,
        "purchased": False,
    }


def test_updates_quantity_for_matching_item():
    items = [_make_item("item-1", 1.0), _make_item("item-2", 1.0)]
    result = _patch_quantity_in_items(items, "item-1", 3.0)
    assert result[0]["quantity"] == 3.0
    assert result[1]["quantity"] == 1.0  # unchanged


def test_leaves_other_fields_intact():
    items = [_make_item("item-1", 1.0)]
    items[0]["name"] = "Latte"
    items[0]["checked"] = True
    result = _patch_quantity_in_items(items, "item-1", 2.0)
    assert result[0]["name"] == "Latte"
    assert result[0]["checked"] is True


def test_raises_404_when_item_not_found():
    from fastapi import HTTPException
    items = [_make_item("item-1")]
    with pytest.raises(HTTPException) as exc_info:
        _patch_quantity_in_items(items, "nonexistent", 2.0)
    assert exc_info.value.status_code == 404


def test_raises_422_when_quantity_below_one():
    from fastapi import HTTPException
    items = [_make_item("item-1")]
    with pytest.raises(HTTPException) as exc_info:
        _patch_quantity_in_items(items, "item-1", 0.0)
    assert exc_info.value.status_code == 422


def test_raises_422_when_quantity_negative():
    from fastapi import HTTPException
    items = [_make_item("item-1")]
    with pytest.raises(HTTPException) as exc_info:
        _patch_quantity_in_items(items, "item-1", -1.0)
    assert exc_info.value.status_code == 422


def test_patch_item_sets_selected_offer_snapshot():
    items = [
        {
            "id": "item-1",
            "name": "Latte",
            "quantity": 1.0,
            "source": "manual",
            "pinned_product_id": None,
            "pinned_offer_id": None,
            "found_deals": [],
        }
    ]
    offer_patch = {
        "source": "offer",
        "pinned_product_id": "prod-1",
        "pinned_offer_id": "offer-1",
        "category": "dairy",
        "subcategory": "Latte",
        "found_deals": [
            {
                "offer_id": "offer-1",
                "product_id": "prod-1",
                "product_name": "Latte intero",
                "supermarket_id": "store-1",
                "supermarket_name": "Lidl",
                "price_offer": 0.99,
            }
        ],
    }

    result = _patch_item_in_items(items, "item-1", offer_patch)

    assert result[0]["source"] == "offer"
    assert result[0]["pinned_offer_id"] == "offer-1"
    assert result[0]["pinned_product_id"] == "prod-1"
    assert result[0]["found_deals"][0]["offer_id"] == "offer-1"
    assert result[0]["found_deals"][0]["supermarket_name"] == "Lidl"


# ---------------------------------------------------------------------------
# Endpoint tests — PATCH /lists/{list_id}/items/{item_id}
# ---------------------------------------------------------------------------

import sys
import os
import types
from unittest.mock import MagicMock, patch

# Stub infrastructure (same pattern as test_favorites_router.py)
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

import httpx
from fastapi import FastAPI
import api.routers.lists as _lists_module
from api.routers.lists import router as _lists_router

_DEP_GET_USER_ID = _lists_module.get_current_user_id

_test_app = FastAPI()
_test_app.include_router(_lists_router, prefix="/lists")

_LIST_ID = "list-abc"
_ITEM_ID = "item-1"
_USER_ID = "user-xyz"


def _deps(user_id: str = _USER_ID) -> dict:
    return {_DEP_GET_USER_ID: lambda: user_id}


async def _patch_req(url: str, json: dict, dep_overrides: dict | None = None) -> httpx.Response:
    _test_app.dependency_overrides = dep_overrides or {}
    transport = httpx.ASGITransport(app=_test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(url, json=json)


async def _post_req(
    url: str,
    json: dict,
    dep_overrides: dict | None = None,
) -> httpx.Response:
    _test_app.dependency_overrides = dep_overrides or {}
    transport = httpx.ASGITransport(app=_test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, json=json)


async def test_patch_quantity_returns_updated_item():
    initial_items = [
        {"id": _ITEM_ID, "name": "Latte", "quantity": 1.0, "checked": False, "purchased": False}
    ]
    sb_mock = MagicMock()
    sb_mock.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "items": initial_items
    }
    sb_mock.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch.object(_lists_module, "get_supabase", return_value=sb_mock), \
         patch.object(_lists_module, "_verify_member", return_value=None):
        resp = await _patch_req(
            f"/lists/{_LIST_ID}/items/{_ITEM_ID}",
            json={"quantity": 3.0},
            dep_overrides=_deps(),
        )

    assert resp.status_code == 200
    assert resp.json()["quantity"] == 3.0
    assert resp.json()["id"] == _ITEM_ID


async def test_patch_selected_offer_returns_coherent_item():
    initial_items = [
        {
            "id": _ITEM_ID,
            "name": "Latte",
            "quantity": 1.0,
            "source": "manual",
            "pinned_product_id": None,
            "pinned_offer_id": None,
            "found_deals": [],
        }
    ]
    offer_patch = {
        "source": "offer",
        "pinned_product_id": "prod-1",
        "pinned_offer_id": "offer-1",
        "category": "dairy",
        "subcategory": "Latte",
        "found_deals": [{"offer_id": "offer-1", "price_offer": 0.99}],
    }
    sb_mock = MagicMock()
    sb_mock.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "items": initial_items
    }

    with patch.object(_lists_module, "get_supabase", return_value=sb_mock), \
         patch.object(_lists_module, "_verify_member", return_value=None), \
         patch.object(_lists_module, "_selected_offer_patch", return_value=offer_patch):
        resp = await _patch_req(
            f"/lists/{_LIST_ID}/items/{_ITEM_ID}",
            json={"pinned_offer_id": "offer-1"},
            dep_overrides=_deps(),
        )

    assert resp.status_code == 200
    assert resp.json()["source"] == "offer"
    assert resp.json()["pinned_offer_id"] == "offer-1"
    assert resp.json()["pinned_product_id"] == "prod-1"
    assert resp.json()["found_deals"][0]["offer_id"] == "offer-1"


async def test_patch_quantity_422_on_zero():
    sb_mock = MagicMock()
    sb_mock.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "items": [{"id": _ITEM_ID, "name": "Latte", "quantity": 1.0, "checked": False, "purchased": False}]
    }
    with patch.object(_lists_module, "get_supabase", return_value=sb_mock), \
         patch.object(_lists_module, "_verify_member", return_value=None):
        resp = await _patch_req(
            f"/lists/{_LIST_ID}/items/{_ITEM_ID}",
            json={"quantity": 0},
            dep_overrides=_deps(),
        )
    assert resp.status_code == 422


async def test_patch_quantity_403_non_member():
    from fastapi import HTTPException
    with patch.object(_lists_module, "get_supabase", return_value=MagicMock()), \
         patch.object(_lists_module, "_verify_member", side_effect=HTTPException(status_code=403, detail="Not a member")):
        resp = await _patch_req(
            f"/lists/{_LIST_ID}/items/{_ITEM_ID}",
            json={"quantity": 2.0},
            dep_overrides=_deps(),
        )
    assert resp.status_code == 403


async def test_reset_list_clears_items_and_returns_updated_list():
    updated_list = {
        "id": _LIST_ID,
        "user_id": _USER_ID,
        "name": "Lista spesa",
        "items": [],
        "is_active": True,
    }
    sb_mock = MagicMock()
    table = sb_mock.table.return_value
    table.select.return_value.eq.return_value.single.return_value.execute.return_value.data = updated_list

    with patch.object(_lists_module, "get_supabase", return_value=sb_mock), \
         patch.object(_lists_module, "_verify_member", return_value=None):
        resp = await _post_req(
            f"/lists/{_LIST_ID}/reset",
            json={},
            dep_overrides=_deps(),
        )

    assert resp.status_code == 200
    assert resp.json()["items"] == []
    sb_mock.table.return_value.update.assert_called_with({"items": []})


async def test_reset_list_403_non_member():
    from fastapi import HTTPException
    with patch.object(_lists_module, "get_supabase", return_value=MagicMock()), \
         patch.object(_lists_module, "_verify_member", side_effect=HTTPException(status_code=403, detail="Not a member")):
        resp = await _post_req(
            f"/lists/{_LIST_ID}/reset",
            json={},
            dep_overrides=_deps(),
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_add_item_403_non_member():
    from fastapi import HTTPException

    with patch.object(_lists_module, "get_supabase", return_value=MagicMock()), \
         patch.object(_lists_module, "_verify_member", side_effect=HTTPException(status_code=403, detail="Not a member")):
        resp = await _post_req(
            f"/lists/{_LIST_ID}/items",
            json={"name": "Latte"},
            dep_overrides=_deps(),
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_toggle_item_403_non_member():
    from fastapi import HTTPException

    _test_app.dependency_overrides = _deps()
    transport = httpx.ASGITransport(app=_test_app)
    with patch.object(_lists_module, "get_supabase", return_value=MagicMock()), \
         patch.object(_lists_module, "_verify_member", side_effect=HTTPException(status_code=403, detail="Not a member")):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/lists/{_LIST_ID}/items/{_ITEM_ID}/toggle")

    assert resp.status_code == 403
