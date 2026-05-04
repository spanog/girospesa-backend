import pytest
from api.routers.lists import _patch_quantity_in_items


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
