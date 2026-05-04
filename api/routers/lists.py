from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.database import get_supabase
from services.deal_freshness import classify_deal_freshness

router = APIRouter()


def _verify_member(sb: object, list_id: str, user_id: str) -> None:
    """Raise 403 if user_id is not a member of list_id."""
    from fastapi import HTTPException
    result = (
        sb.table("list_members")  # type: ignore[union-attr,attr-defined]
        .select("id")
        .eq("list_id", list_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=403, detail="Not a member of this list")


class CreateListBody(BaseModel):
    name: str = "Lista spesa"


class AddItemBody(BaseModel):
    name: str
    quantity: float = 1.0
    unit: str | None = None
    source: Literal["manual", "offer"] = "manual"
    pinned_product_id: str | None = None  # canonical products.id (set when source='offer')
    pinned_offer_id: str | None = None    # specific offers.id (set when source='offer')


class InviteBody(BaseModel):
    email: str | None = None


class UpdateItemQuantityBody(BaseModel):
    quantity: float


def _product_categories(sb: object, product_ids: set[str]) -> dict[str, dict]:
    if not product_ids:
        return {}
    rows = (
        sb.table("products")  # type: ignore[union-attr,attr-defined]
        .select("id, category, subcategory")
        .in_("id", sorted(product_ids))
        .execute()
        .data
    )
    return {row["id"]: row for row in rows}


def _offer_categories(sb: object, offer_ids: set[str]) -> dict[str, dict]:
    if not offer_ids:
        return {}
    rows = (
        sb.table("offers")  # type: ignore[union-attr,attr-defined]
        .select("id, product_id, products(category, subcategory)")
        .in_("id", sorted(offer_ids))
        .execute()
        .data
    )
    return {row["id"]: row.get("products") or {} for row in rows}


def _category_for_item(item: dict, products: dict, offers: dict) -> dict:
    product_id = item.get("pinned_product_id")
    product = products.get(product_id) if product_id else None
    category_source = product or offers.get(item.get("pinned_offer_id")) or {}
    return {
        **item,
        "category": category_source.get("category", item.get("category")),
        "subcategory": category_source.get("subcategory", item.get("subcategory")),
    }


def _enrich_items_with_categories(sb: object, items: list[dict]) -> list[dict]:
    product_ids = {item["pinned_product_id"] for item in items if item.get("pinned_product_id")}
    offer_ids = {item["pinned_offer_id"] for item in items if item.get("pinned_offer_id")}
    products = _product_categories(sb, product_ids)
    offers = _offer_categories(sb, offer_ids)
    return [_category_for_item(item, products, offers) for item in items]


def _patch_quantity_in_items(
    items: list[dict], item_id: str, quantity: float
) -> list[dict]:
    if quantity < 1:
        raise HTTPException(status_code=422, detail="quantity must be >= 1")
    updated = []
    found = False
    for item in items:
        if item["id"] == item_id:
            updated.append({**item, "quantity": quantity})
            found = True
        else:
            updated.append(item)
    if not found:
        raise HTTPException(status_code=404, detail="Item not found")
    return updated


@router.get("/active")
async def get_active_list(user_id: Annotated[str, Depends(get_current_user_id)]) -> dict:
    """Return the user's active shopping list (most recent). Creates one if none exists."""
    sb = get_supabase()
    resp = (
        sb.table("shopping_lists")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_active", True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data:
        row = resp.data[0]
        row["items"] = _enrich_items_with_categories(sb, row.get("items") or [])
        return row

    # Auto-create — use explicit user_id; service-role client has no auth.uid()
    new_list = (
        sb.table("shopping_lists")
        .insert({"user_id": user_id, "name": "Lista spesa", "is_active": True, "items": []})
        .execute()
        .data[0]
    )
    new_list_id = new_list["id"]
    sb.table("list_members").insert({
        "list_id": new_list_id,
        "user_id": user_id,
        "role": "owner",
    }).execute()
    row = sb.table("shopping_lists").select("*").eq("id", new_list_id).single().execute().data
    row["items"] = _enrich_items_with_categories(sb, row.get("items") or [])
    return row


@router.post("/{list_id}/items", status_code=status.HTTP_201_CREATED)
async def add_item(
    list_id: str,
    body: AddItemBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    import uuid

    sb = get_supabase()
    new_item = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "quantity": body.quantity,
        "unit": body.unit,
        "checked": False,
        "checked_by": None,
        "checked_at": None,
        "added_by": user_id,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "source": body.source,
        "pinned_product_id": body.pinned_product_id,
        "pinned_offer_id": body.pinned_offer_id,
        "category": None,
        "subcategory": None,
        "found_deals": [],
    }
    new_item = _enrich_items_with_categories(sb, [new_item])[0]
    sb.rpc("update_list_item", {
        "p_list_id": list_id,
        "p_item_id": new_item["id"],
        "p_patch": new_item,
    }).execute()
    # Simpler: just append via jsonb_array_append
    sb.table("shopping_lists").update({
        "items": sb.table("shopping_lists")
        .select("items")
        .eq("id", list_id)
        .single()
        .execute()
        .data["items"] + [new_item]
    }).eq("id", list_id).execute()
    return new_item


@router.delete("/{list_id}/items/{item_id}")
async def remove_item(
    list_id: str,
    item_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    # Verify user is a member of the list before modifying
    _verify_member(sb, list_id, user_id)
    current = sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    items = [i for i in current.data["items"] if i["id"] != item_id]
    sb.table("shopping_lists").update({"items": items}).eq("id", list_id).execute()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{list_id}/items/{item_id}/toggle")
async def toggle_item(
    list_id: str,
    item_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    current = sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    items = current.data["items"]
    updated = []
    toggled = None
    for item in items:
        if item["id"] == item_id:
            new_checked = not item.get("checked", False)
            item = {
                **item,
                "checked": new_checked,
                "checked_by": user_id if new_checked else None,
                "checked_at": datetime.now(timezone.utc).isoformat() if new_checked else None,
            }
            toggled = item
        updated.append(item)
    sb.table("shopping_lists").update({"items": updated}).eq("id", list_id).execute()
    if toggled is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return toggled


@router.patch("/{list_id}/items/{item_id}")
async def patch_item_quantity(
    list_id: str,
    item_id: str,
    body: UpdateItemQuantityBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    current = sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    updated_items = _patch_quantity_in_items(current.data["items"], item_id, body.quantity)
    sb.table("shopping_lists").update({"items": updated_items}).eq("id", list_id).execute()
    return next(i for i in updated_items if i["id"] == item_id)


@router.post("/{list_id}/invite")
async def create_invite(
    list_id: str,
    body: InviteBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    # Ensure requester is owner
    member = (
        sb.table("list_members")
        .select("role")
        .eq("list_id", list_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not member.data or member.data[0]["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can invite")

    invite = (
        sb.table("list_invites")
        .insert({"list_id": list_id, "invited_by": user_id, "email": body.email})
        .execute()
    )
    return invite.data[0]


@router.get("/{list_id}/members")
async def list_members(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> list[dict]:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    members = (
        sb.table("list_members")
        .select("*")
        .eq("list_id", list_id)
        .execute()
        .data
    )
    member_ids = [m["user_id"] for m in members]
    profiles = (
        sb.table("user_profiles")
        .select("id, display_name, avatar_url")
        .in_("id", member_ids)
        .execute()
        .data
    ) if member_ids else []
    profiles_by_id = {p["id"]: p for p in profiles}
    for m in members:
        m["user_profiles"] = profiles_by_id.get(m["user_id"])
    return members


@router.get("/{list_id}/deal-freshness")
async def get_deal_freshness(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> list[dict]:
    """Return freshness classification for every pinned offer in the list."""
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)

    list_row = (
        sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    )
    items: list[dict] = list_row.data.get("items") or []

    offer_ids = [
        item["pinned_offer_id"]
        for item in items
        if item.get("pinned_offer_id")
    ]

    offers_by_id: dict[str, dict] = {}
    if offer_ids:
        rows = (
            sb.table("offers")
            .select("id, price_offer, valid_to, is_active")
            .in_("id", offer_ids)
            .execute()
        ).data
        offers_by_id = {row["id"]: row for row in rows}

    return [dict(entry) for entry in classify_deal_freshness(items, offers_by_id)]


@router.delete("/{list_id}/members/{member_user_id}")
async def remove_member(
    list_id: str,
    member_user_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    owner = (
        sb.table("list_members")
        .select("role")
        .eq("list_id", list_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not owner.data or owner.data["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can remove members")
    if member_user_id == user_id:
        raise HTTPException(status_code=400, detail="Owner cannot remove themselves")
    sb.table("list_members").delete().eq("list_id", list_id).eq("user_id", member_user_id).execute()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
