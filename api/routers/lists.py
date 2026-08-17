from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Annotated, Literal
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.auth import get_current_access_token, get_current_user_id
from core.config import settings
from core.database import get_postgres_cursor, get_supabase, has_direct_postgres
from services.repositories import lists_repository as repo
from services.extraction.normalizer import format_unit_price_label
from services.deal_freshness import classify_deal_freshness, offer_is_active_now
from services.list_offer_visibility import (
    HIDDEN_FOR_VIEWER,
    hidden_offer_ids_for_viewer,
    project_item_for_viewer,
    project_items_for_viewer,
    project_items_without_offers,
    visible_supermarket_ids_for_user,
)
from services.offer_visibility import apply_current_offer_window
from services.push_notify import send_push_to_user
from services.list_sync import (
    LIST_SYNC_HEARTBEAT_SECONDS,
    connect_listener,
    now_utc_iso,
    publish_list_sync_event,
    wait_for_list_sync_event,
)

router = APIRouter()
DEFAULT_LIST_NAME = "La mia lista"

PRODUCT_SUBCATEGORIES = {
    "alimentari-freschi": {
        "Latticini e Formaggi",
        "Macelleria e Polleria",
        "Salumeria e Gastronomia",
        "Ortofrutta",
        "Pescheria",
    },
    "dispensa": {
        "Primi Piatti e Preparati",
        "Condimenti e Conserve",
        "Conserve Ittiche e di Carne",
        "Colazione e Prodotti da Forno",
        "Caffè Tè e Tisane",
        "Snack Salati e Dolciumi",
    },
    "surgelati": {
        "Pesce e Frutti di Mare",
        "Verdure e Preparati",
        "Piatti Pronti e Pizze",
        "Gelati",
    },
    "bevande": {
        "Acqua e Bibite",
        "Succhi e Bevande alla frutta",
        "Alcolici e Birre",
    },
    "cura-persona-salute": {
        "Igiene Orale",
        "Igiene Corpo e Capelli",
        "Igiene Intima e Salute",
        "Infanzia",
        "Integratori e Parafarmacia",
    },
    "cura-casa": {
        "Detergenti Bucato e Stoviglie",
        "Pulizia Superfici e Cura Ambienti",
        "Carta e Monouso",
        "Accessori e Manutenzione casa",
    },
    "prodotti-animali": {
        "Alimentazione Cane e Gatto",
        "Alimentazione Piccoli Animali",
        "Igiene e Accessori Animali",
    },
    "altro": set(),
}

PRODUCT_CATEGORIES = set(PRODUCT_SUBCATEGORIES)


def _verify_member(sb: object, list_id: str, user_id: str) -> None:
    repo.verify_member(sb, list_id, user_id)


def _verify_owner(sb: object, list_id: str, user_id: str) -> None:
    repo.verify_owner(sb, list_id, user_id)


class AddItemBody(BaseModel):
    name: str
    brand: str | None = None
    quantity: float = 1.0
    unit: str | None = None
    source: Literal["manual", "offer"] = "manual"
    pinned_offer_id: str | None = None    # specific offers.id (set when source='offer')
    image_url: str | None = None


class InviteByEmailBody(BaseModel):
    email: str


class SelectListBody(BaseModel):
    list_id: str


class UpdateListItemBody(BaseModel):
    quantity: float | None = None
    pinned_offer_id: str | None = None
    category: str | None = None
    subcategory: str | None = None


def _now_utc() -> str:
    return now_utc_iso()


def _is_past_timestamp(value: str | datetime | None) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        return value < datetime.now(timezone.utc)
    return value < _now_utc()


def _offer_categories(sb: object, offer_ids: set[str]) -> dict[str, dict]:
    if not offer_ids:
        return {}
    rows = (
        sb.table("offers")  # type: ignore[union-attr,attr-defined]
        .select("id, brand, category, subcategory")
        .in_("id", sorted(offer_ids))
        .execute()
        .data
    )
    return {row["id"]: row for row in rows}


def _category_for_item(item: dict, offers: dict) -> dict:
    category_source = offers.get(item.get("pinned_offer_id")) or {}
    return {
        **item,
        "brand": category_source.get("brand", item.get("brand")),
        "category": category_source.get("category", item.get("category")),
        "subcategory": category_source.get("subcategory", item.get("subcategory")),
    }


def _enrich_items_with_categories(sb: object, items: list[dict]) -> list[dict]:
    offer_ids = {item["pinned_offer_id"] for item in items if item.get("pinned_offer_id")}
    offers = _offer_categories(sb, offer_ids)
    return [_category_for_item(item, offers) for item in items]


def _patch_quantity_in_items(
    items: list[dict], item_id: str, quantity: float
) -> list[dict]:
    return _patch_item_in_items(items, item_id, {"quantity": quantity})


def _patch_item_in_items(
    items: list[dict], item_id: str, patch: dict
) -> list[dict]:
    quantity = patch.get("quantity")
    if quantity is not None and quantity < 1:
        raise HTTPException(status_code=422, detail="quantity must be >= 1")
    if patch.get("category") is None and "category" in patch:
        patch = {**patch, "subcategory": None}
    updated = []
    found = False
    for item in items:
        if item["id"] == item_id:
            updated.append({**item, **patch})
            found = True
        else:
            updated.append(item)
    if not found:
        raise HTTPException(status_code=404, detail="Item not found")
    return updated


def _validated_category_patch(
    body: UpdateListItemBody, current_item: dict
) -> dict:
    patch: dict = {}
    fields = body.model_fields_set
    if "category" in fields:
        patch["category"] = body.category
    if "subcategory" in fields:
        patch["subcategory"] = body.subcategory
    if not patch:
        return patch
    category = patch.get("category", current_item.get("category"))
    subcategory = patch.get("subcategory", current_item.get("subcategory"))
    _validate_category_values(category, subcategory)
    if category is None:
        patch["subcategory"] = None
    if category == "altro":
        patch["subcategory"] = None
    return patch


def _validate_category_values(category: str | None, subcategory: str | None) -> None:
    if category is None:
        if subcategory is not None:
            raise HTTPException(status_code=422, detail="subcategory requires category")
        return
    if category not in PRODUCT_CATEGORIES:
        raise HTTPException(status_code=422, detail="invalid category")
    if subcategory and subcategory not in PRODUCT_SUBCATEGORIES[category]:
        raise HTTPException(status_code=422, detail="invalid subcategory")


def _find_item(items: list[dict], item_id: str) -> dict:
    for item in items:
        if item["id"] == item_id:
            return item
    raise HTTPException(status_code=404, detail="Item not found")


def _deal_snapshot_from_offer(offer: dict) -> dict:
    supermarket = offer.get("supermarkets") or {}
    return {
        "offer_id": offer["id"],
        "product_name": offer.get("name"),
        "supermarket_id": offer.get("supermarket_id"),
        "supermarket_name": supermarket.get("name"),
        "price_offer": offer.get("price_offer"),
        "price_original": offer.get("price_original"),
        "discount_pct": offer.get("discount_pct"),
        "unit_price": offer.get("unit_price"),
        "unit_price_value": offer.get("unit_price_value"),
        "unit_price_unit": offer.get("unit_price_unit"),
        "unit_price_label": offer.get("unit_price") or format_unit_price_label(
            offer.get("unit_price_value"),
            offer.get("unit_price_unit"),
        ),
        "format_label": offer.get("format_label") or None,
        "valid_to": offer.get("valid_to"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _offer_row(sb: object, offer_id: str) -> dict:
    rows = (
        sb.table("offers")  # type: ignore[union-attr,attr-defined]
        .select(
            "id, name, brand, category, subcategory, image_url, supermarket_id, price_offer, price_original, "
            "discount_pct, unit_price, unit_price_value, unit_price_unit, "
            "valid_to, format_label"
        )
        .eq("id", offer_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Offer not found")
    return rows[0]


def _supermarket_row(sb: object, supermarket_id: str | None) -> dict:
    if not supermarket_id:
        return {}
    rows = (
        sb.table("supermarkets")  # type: ignore[union-attr,attr-defined]
        .select("id, name")
        .eq("id", supermarket_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else {}


def _selected_offer_patch(sb: object, offer_id: str) -> dict:
    offer = _offer_row(sb, offer_id)
    supermarket = _supermarket_row(sb, offer.get("supermarket_id"))
    offer = {
        **offer,
        "supermarkets": supermarket,
    }
    return {
        "source": "offer",
        "name": offer.get("name", ""),
        "brand": offer.get("brand"),
        "pinned_offer_id": offer["id"],
        "image_url": offer.get("image_url"),
        "category": offer.get("category"),
        "subcategory": offer.get("subcategory"),
        "found_deals": [_deal_snapshot_from_offer(offer)],
    }


async def _rpc_update_list_item(
    list_id: str,
    item_id: str,
    patch: dict,
    user_id: str,
    access_token: str,
) -> None:
    await _rpc_call("update_list_item", {
        "p_list_id": list_id,
        "p_item_id": item_id,
        "p_patch": patch,
    }, user_id, access_token)


async def _rpc_append_list_item(
    list_id: str,
    item: dict,
    user_id: str,
    access_token: str,
) -> None:
    await _rpc_call("append_list_item", {
        "p_list_id": list_id,
        "p_item": item,
    }, user_id, access_token)


async def _rpc_remove_list_item(
    list_id: str,
    item_id: str,
    user_id: str,
    access_token: str,
) -> None:
    await _rpc_call("remove_list_item", {
        "p_list_id": list_id,
        "p_item_id": item_id,
    }, user_id, access_token)


async def _rpc_call(
    function_name: str,
    payload: dict,
    user_id: str,
    access_token: str,
) -> None:
    if has_direct_postgres():
        _direct_rpc_call(function_name, payload, user_id)
        return

    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/rpc/{function_name}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=payload, headers=headers)
    if response.status_code in (200, 204):
        return
    raise HTTPException(status_code=500, detail=f"Failed to call RPC {function_name}")


def _direct_rpc_call(function_name: str, payload: dict, user_id: str) -> None:
    # Membership was already verified by the caller via _verify_member.
    # Run as postgres superuser (bypasses RLS) — no auth.uid() needed here.
    with get_postgres_cursor() as cursor:
        if function_name == "append_list_item":
            cursor.execute(
                """
                UPDATE public.shopping_lists
                SET items = CASE
                    WHEN %s::jsonb->>'source' = 'offer'
                        AND %s::jsonb->>'pinned_offer_id' IS NOT NULL
                        AND EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(COALESCE(items, '[]'::jsonb)) item
                            WHERE item->>'source' = 'offer'
                              AND item->>'pinned_offer_id' = %s::jsonb->>'pinned_offer_id'
                              AND COALESCE((item->>'purchased')::boolean, false) = false
                        )
                    THEN (
                        SELECT jsonb_agg(
                            CASE
                                WHEN item->>'source' = 'offer'
                                    AND item->>'pinned_offer_id' = %s::jsonb->>'pinned_offer_id'
                                    AND COALESCE((item->>'purchased')::boolean, false) = false
                                    AND item->>'id' = (
                                        SELECT candidate->>'id'
                                        FROM jsonb_array_elements(COALESCE(items, '[]'::jsonb)) candidate
                                        WHERE candidate->>'source' = 'offer'
                                          AND candidate->>'pinned_offer_id' = %s::jsonb->>'pinned_offer_id'
                                          AND COALESCE((candidate->>'purchased')::boolean, false) = false
                                        LIMIT 1
                                    )
                                THEN jsonb_set(
                                    item,
                                    '{quantity}',
                                    to_jsonb(
                                        COALESCE((item->>'quantity')::numeric, 0)
                                        + COALESCE((%s::jsonb->>'quantity')::numeric, 1)
                                    )
                                )
                                ELSE item
                            END
                            ORDER BY position
                        )
                        FROM jsonb_array_elements(COALESCE(items, '[]'::jsonb))
                            WITH ORDINALITY AS entries(item, position)
                    )
                    ELSE COALESCE(items, '[]'::jsonb) || jsonb_build_array(%s::jsonb)
                END,
                    updated_at = now()
                WHERE id = %s
                """,
                (
                    json.dumps(payload["p_item"]),
                    json.dumps(payload["p_item"]),
                    json.dumps(payload["p_item"]),
                    json.dumps(payload["p_item"]),
                    json.dumps(payload["p_item"]),
                    json.dumps(payload["p_item"]),
                    json.dumps(payload["p_item"]),
                    payload["p_list_id"],
                ),
            )
            return
        if function_name == "remove_list_item":
            cursor.execute(
                """
                UPDATE public.shopping_lists
                SET items = COALESCE(
                    (SELECT jsonb_agg(item)
                     FROM jsonb_array_elements(items) AS item
                     WHERE item->>'id' <> %s),
                    '[]'::jsonb),
                    updated_at = now()
                WHERE id = %s
                """,
                (payload["p_item_id"], payload["p_list_id"]),
            )
            return
        if function_name == "update_list_item":
            cursor.execute(
                """
                UPDATE public.shopping_lists
                SET items = (
                    SELECT jsonb_agg(
                        CASE WHEN item->>'id' = %s THEN item || %s::jsonb ELSE item END
                    )
                    FROM jsonb_array_elements(items) AS item
                ),
                updated_at = now()
                WHERE id = %s
                """,
                (
                    payload["p_item_id"],
                    json.dumps(payload["p_patch"]),
                    payload["p_list_id"],
                ),
            )
            return
    raise HTTPException(status_code=500, detail=f"Unsupported RPC {function_name}")


def _profile_row(sb: object, user_id: str) -> dict:
    return repo.profile_row(sb, user_id)


def _active_list_id_for_user(sb: object, user_id: str) -> str | None:
    return repo.active_list_id_for_user(sb, user_id)


def _set_active_list_id(user_id: str, list_id: str | None) -> None:
    repo.set_active_list_id(user_id, list_id)


def _fallback_selected_list_for_users(
    sb: object,
    user_ids: set[str],
    removed_list_id: str,
) -> None:
    for target_user_id in user_ids:
        if _active_list_id_for_user(sb, target_user_id) != removed_list_id:
            continue
        fallback_list_id = repo.owner_list_id_for_user(sb, target_user_id)
        _set_active_list_id(target_user_id, fallback_list_id)


def _create_owned_list(
    *,
    user_id: str,
    name: str,
    is_active: bool = True,
    items: list[dict] | None = None,
) -> dict:
    return repo.create_owned_list(
        user_id=user_id,
        name=name,
        is_active=is_active,
        items=items,
    )


def _shopping_list_row(list_id: str) -> dict:
    return repo.shopping_list_row(list_id)


def _shopping_list_rows(list_ids: list[str]) -> list[dict]:
    return repo.shopping_list_rows(list_ids)

def _visible_memberships(sb: object, user_id: str) -> list[dict]:
    return repo.visible_memberships(sb, user_id)

def _resolve_list_id_for_user(sb: object, user_id: str) -> str | None:
    return repo.resolved_list_id_for_user(sb, user_id)


def _list_member_role(sb: object, list_id: str, user_id: str) -> str | None:
    return repo.list_member_role(sb, list_id, user_id)


def _workspace_display_name(
    *,
    stored_name: str | None,
    is_owner: bool,
    owner_display_name: str | None,
) -> str:
    if is_owner:
        return DEFAULT_LIST_NAME
    owner_name = (owner_display_name or "").strip()
    if owner_name:
        return f"La lista di {owner_name}"
    return stored_name or "Lista condivisa"


def _list_summary(
    sb: object,
    row: dict,
    user_id: str,
    member_count: int,
    owner_display_name: str | None = None,
) -> dict:
    member_role = _list_member_role(sb, row["id"], user_id)
    is_owner = member_role == "owner"
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": _workspace_display_name(
            stored_name=row.get("name"),
            is_owner=is_owner,
            owner_display_name=owner_display_name,
        ),
        "owner_display_name": owner_display_name,
        "is_active": row.get("is_active", True),
        "member_role": member_role,
        "is_owner": is_owner,
        "member_count": member_count,
        "item_count": len(row.get("items") or []),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _list_detail(sb: object, list_id: str, user_id: str) -> dict:
    _verify_member(sb, list_id, user_id)
    row = _shopping_list_row(list_id)
    items = _enrich_items_with_categories(sb, row.get("items") or [])
    row["items"] = _project_list_items_for_viewer(sb, items, user_id)
    member_role = _list_member_role(sb, list_id, user_id)
    owner_display_name = repo.profile_row(sb, row["user_id"]).get("display_name")
    row["member_role"] = member_role
    row["is_owner"] = member_role == "owner"
    row["owner_display_name"] = owner_display_name
    row["name"] = _workspace_display_name(
        stored_name=row.get("name"),
        is_owner=row["is_owner"],
        owner_display_name=owner_display_name,
    )
    return row


def _offer_rows_for_items(sb: object, items: list[dict]) -> list[dict]:
    offer_ids = sorted({
        item["pinned_offer_id"]
        for item in items
        if item.get("pinned_offer_id")
    })
    if not offer_ids:
        return []
    return (
        sb.table("offers")  # type: ignore[union-attr,attr-defined]
        .select("id, supermarket_id, valid_from, valid_to, is_active")
        .in_("id", offer_ids)
        .execute()
        .data
    )


def _hidden_offer_ids_for_viewer(
    sb: object,
    items: list[dict],
    user_id: str,
) -> set[str]:
    visible_supermarket_ids = visible_supermarket_ids_for_user(sb, user_id)
    if visible_supermarket_ids is None:
        return set()
    offer_rows = _offer_rows_for_items(sb, items)
    return hidden_offer_ids_for_viewer(items, offer_rows, visible_supermarket_ids)


def _project_list_items_for_viewer(
    sb: object,
    items: list[dict],
    user_id: str,
) -> list[dict]:
    offer_rows = _offer_rows_for_items(sb, items)
    hidden_offer_ids = _hidden_offer_ids_for_viewer_from_rows(items, user_id, sb, offer_rows)
    stale_offer_ids = _stale_offer_ids_for_viewer(items, offer_rows, hidden_offer_ids)
    projected_items = project_items_for_viewer(items, hidden_offer_ids)
    return project_items_without_offers(projected_items, stale_offer_ids)


def _hidden_offer_ids_for_viewer_from_rows(
    items: list[dict],
    user_id: str,
    sb: object,
    offer_rows: list[dict],
) -> set[str]:
    visible_supermarket_ids = visible_supermarket_ids_for_user(sb, user_id)
    if visible_supermarket_ids is None:
        return set()
    return hidden_offer_ids_for_viewer(items, offer_rows, visible_supermarket_ids)


def _stale_offer_ids_for_viewer(
    items: list[dict],
    offer_rows: list[dict],
    hidden_offer_ids: set[str],
) -> set[str]:
    offers_by_id = {row["id"]: row for row in offer_rows}
    stale_offer_ids: set[str] = set()
    for item in items:
        offer_id = item.get("pinned_offer_id")
        if not offer_id or offer_id in hidden_offer_ids:
            continue
        offer_row = offers_by_id.get(offer_id)
        if not offer_row or not offer_is_active_now(offer_row):
            stale_offer_ids.add(offer_id)
    return stale_offer_ids


def _member_counts(list_ids: list[str]) -> dict[str, int]:
    return repo.member_counts(list_ids)


def _invite_payload(list_name: str, inviter_name: str | None, invite_id: str, list_id: str) -> dict:
    return {
        "invite_id": invite_id,
        "invite_status": "pending",
        "list_id": list_id,
        "url": f"/lista?panel=inviti",
        "list_name": list_name,
        "invited_by": inviter_name,
    }


def _list_invite_accepted_payload(
    list_name: str,
    accepted_by: str,
    list_id: str,
) -> dict:
    return {
        "list_id": list_id,
        "list_name": list_name,
        "accepted_by": accepted_by,
        "url": "/lista",
    }


def _list_member_removed_payload(list_name: str, removed_by: str | None, list_id: str) -> dict:
    return {
        "list_id": list_id,
        "list_name": list_name,
        "removed_by": removed_by,
        "removed_by_email": None,
        "url": "/lista",
    }


def _list_member_left_payload(list_name: str, left_by: str | None, list_id: str) -> dict:
    return {
        "list_id": list_id,
        "list_name": list_name,
        "left_by": left_by,
        "left_by_email": None,
        "url": "/lista",
    }


def _create_app_notification(
    sb: object,
    user_id: str,
    *,
    kind: str,
    title: str,
    body: str,
    data: dict,
) -> dict:
    return repo.create_app_notification(user_id, kind=kind, title=title, body=body, data=data)


def _notify_shared_list_event(
    sb: object,
    user_id: str,
    *,
    kind: str,
    title: str,
    body: str,
    data: dict,
) -> dict | None:
    notification = _create_app_notification(
        sb,
        user_id,
        kind=kind,
        title=title,
        body=body,
        data=data,
    )
    _notify_invited_user(sb, user_id, title, body, data)
    return notification


def _mark_invite_notifications_read(sb: object, invite_id: str, user_id: str) -> None:
    repo.mark_invite_notifications_read(invite_id, user_id)


def _update_invite_notifications(
    sb: object,
    invite_id: str,
    user_id: str,
    *,
    title: str,
    body: str,
    data: dict,
) -> None:
    repo.update_invite_notifications(
        invite_id,
        user_id,
        title=title,
        body=body,
        data=data,
    )


def _pending_list_invites_for_user(user_id: str) -> list[dict]:
    return repo.pending_list_invites_for_user(user_id)


def _list_invites_for_user(user_id: str) -> list[dict]:
    return repo.list_invites_for_user(user_id)


def _invite_for_user(
    invite_id: str,
    user_id: str,
    *,
    pending_only: bool = True,
) -> dict | None:
    return repo.invite_for_user(invite_id, user_id, pending_only=pending_only)


def _existing_member(list_id: str, user_id: str) -> bool:
    return repo.existing_member(list_id, user_id)


def _insert_member(list_id: str, user_id: str, role: str, invited_by: str | None = None) -> None:
    repo.insert_member(list_id, user_id, role, invited_by)


def _delete_member(list_id: str, user_id: str) -> None:
    repo.delete_member(list_id, user_id)


def _set_invite_status(invite_id: str, *, status: str, accepted_by: str | None = None) -> None:
    repo.set_invite_status(invite_id, status=status, accepted_by=accepted_by)


def _pending_invite_for_target(list_id: str, invited_user_id: str) -> dict | None:
    return repo.pending_invite_for_target(list_id, invited_user_id)


def _insert_list_invite(list_id: str, invited_by: str, invited_user_id: str, email: str) -> dict:
    return repo.insert_list_invite(list_id, invited_by, invited_user_id, email)


def _auth_user_by_email(email: str) -> dict | None:
    return repo.auth_user_by_email(email)


def _auth_user_by_id(user_id: str) -> dict | None:
    return repo.auth_user_by_id(user_id)


def _publish_list_sync_event(
    list_id: str,
    event: Literal["list_updated", "members_updated", "invites_updated"],
    reason: str,
) -> None:
    publish_list_sync_event(list_id, event, reason)


def _format_sse_message(
    event: str,
    payload: dict,
    *,
    event_id: str | None = None,
) -> str:
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(payload)}")
    return "\n".join(lines) + "\n\n"


def _format_notification_actor(display_name: str | None, email: str | None) -> str:
    name = display_name.strip() if isinstance(display_name, str) and display_name.strip() else None
    mail = email.strip() if isinstance(email, str) and email.strip() else None
    if name and mail and name != mail:
        return f"{name} ({mail})"
    if name:
        return name
    if mail:
        return mail
    return "Un utente"


def _impacted_member_user_ids_for_list(list_id: str) -> list[str]:
    return repo.impacted_member_user_ids_for_list(list_id)


def _notify_invited_user(sb: object, user_id: str, title: str, body: str, data: dict) -> None:
    send_push_to_user(sb, user_id=user_id, title=title, body=body, data=data)


def _raise_invalid_invite_status(invite: dict) -> None:
    status_value = invite.get("status")
    status = status_value if isinstance(status_value, str) else "unknown"
    if status == "expired" or _is_past_timestamp(invite.get("expires_at")):
        raise HTTPException(status_code=410, detail="Invite has expired")
    if status == "revoked":
        raise HTTPException(status_code=409, detail="Invite has been revoked")
    if status == "accepted":
        raise HTTPException(status_code=409, detail="Invite has already been accepted")
    if status == "declined":
        raise HTTPException(status_code=409, detail="Invite has already been declined")
    raise HTTPException(status_code=409, detail=f"Invite is not actionable: {status}")


@router.get("")
async def list_lists(user_id: Annotated[str, Depends(get_current_user_id)]) -> list[dict]:
    sb = get_supabase()
    resolved_list_id = _resolve_list_id_for_user(sb, user_id)
    if resolved_list_id is None:
        new_list = _create_owned_list(
            user_id=user_id,
            name=DEFAULT_LIST_NAME,
            items=[],
            is_active=True,
        )
        resolved_list_id = new_list["id"]
        _set_active_list_id(user_id, resolved_list_id)

    memberships = _visible_memberships(sb, user_id)
    list_ids = [row["list_id"] for row in memberships]
    if resolved_list_id not in list_ids:
        list_ids.append(resolved_list_id)
    rows = _shopping_list_rows(list_ids)
    rows_by_id = {row["id"]: row for row in rows}
    counts = repo.member_counts(list_ids)
    owner_display_names = repo.owner_display_names(list_ids)
    summaries = [
        {
            **_list_summary(
                sb,
                rows_by_id[list_id],
                user_id,
                counts.get(list_id, 1),
                owner_display_names.get(list_id),
            ),
            "is_selected": list_id == resolved_list_id,
        }
        for list_id in list_ids
        if list_id in rows_by_id
    ]
    summaries.sort(
        key=lambda row: (
            0 if row["is_selected"] else 1,
            0 if row["is_owner"] else 1,
            (row.get("name") or "").lower(),
        )
    )
    return summaries


@router.post("/select")
async def select_active_list(
    body: SelectListBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, body.list_id, user_id)
    _set_active_list_id(user_id, body.list_id)
    return {"list_id": body.list_id}


@router.get("/active")
async def get_active_list(user_id: Annotated[str, Depends(get_current_user_id)]) -> dict:
    """Return the single visible shopping list. Creates owner list if missing."""
    sb = get_supabase()
    resolved_list_id = _resolve_list_id_for_user(sb, user_id)
    if resolved_list_id:
        if _active_list_id_for_user(sb, user_id) != resolved_list_id:
            _set_active_list_id(user_id, resolved_list_id)
        return _list_detail(sb, resolved_list_id, user_id)

    new_list = _create_owned_list(
        user_id=user_id,
        name=DEFAULT_LIST_NAME,
        items=[],
        is_active=True,
    )
    _set_active_list_id(user_id, new_list["id"])
    return _list_detail(sb, new_list["id"], user_id)


@router.get("/invites/pending")
async def list_pending_invites(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> list[dict]:
    sb = get_supabase()
    invites = _pending_list_invites_for_user(user_id)
    if not invites:
        return []
    list_ids = sorted({invite["list_id"] for invite in invites})
    inviter_ids = sorted({invite["invited_by"] for invite in invites})
    lists = (
        sb.table("shopping_lists")
        .select("id, name")
        .in_("id", list_ids)
        .execute()
        .data
    )
    profiles = (
        sb.table("user_profiles")
        .select("id, display_name")
        .in_("id", inviter_ids)
        .execute()
        .data
    )
    list_names = {row["id"]: row["name"] for row in lists}
    inviter_names = {row["id"]: row.get("display_name") for row in profiles}
    for invite in invites:
        invite["list_name"] = list_names.get(invite["list_id"])
        invite["invited_by_name"] = inviter_names.get(invite["invited_by"])
    return invites


@router.get("/invites")
async def list_received_invites(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> list[dict]:
    sb = get_supabase()
    invites = _list_invites_for_user(user_id)
    if not invites:
        return []
    list_ids = sorted({invite["list_id"] for invite in invites})
    inviter_ids = sorted({invite["invited_by"] for invite in invites})
    lists = (
        sb.table("shopping_lists")
        .select("id, name")
        .in_("id", list_ids)
        .execute()
        .data
    )
    profiles = (
        sb.table("user_profiles")
        .select("id, display_name")
        .in_("id", inviter_ids)
        .execute()
        .data
    )
    list_names = {row["id"]: row["name"] for row in lists}
    inviter_names = {row["id"]: row.get("display_name") for row in profiles}
    for invite in invites:
        invite["list_name"] = list_names.get(invite["list_id"])
        invite["invited_by_name"] = inviter_names.get(invite["invited_by"])
    return invites


@router.post("/invites/{invite_id}/accept")
async def accept_pending_invite(
    invite_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    invite = _invite_for_user(invite_id, user_id, pending_only=False)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.get("status") != "pending":
        _raise_invalid_invite_status(invite)
    if _is_past_timestamp(invite.get("expires_at")):
        _set_invite_status(invite_id, status="expired")
        _mark_invite_notifications_read(sb, invite_id, user_id)
        raise HTTPException(status_code=410, detail="Invite has expired")
    if not _existing_member(invite["list_id"], user_id):
        _insert_member(invite["list_id"], user_id, "member", invite.get("invited_by"))
    list_row = _shopping_list_row(invite["list_id"])
    member_profile = _profile_row(sb, user_id)
    member_name = member_profile.get("display_name") or "Un utente"
    _set_active_list_id(user_id, invite["list_id"])
    _set_invite_status(invite_id, status="accepted", accepted_by=user_id)
    _mark_invite_notifications_read(sb, invite_id, user_id)
    _notify_shared_list_event(
        sb,
        invite["invited_by"],
        kind="list_invite_accepted",
        title="Invito accettato",
        body=f"{member_name} ha accettato il tuo invito: adesso condivide la tua lista",
        data=_list_invite_accepted_payload(
            list_row["name"],
            member_name,
            invite["list_id"],
        ),
    )
    _publish_list_sync_event(invite["list_id"], "members_updated", "member_joined")
    _publish_list_sync_event(invite["list_id"], "invites_updated", "invite_accepted")
    return {"list_id": invite["list_id"]}


@router.post("/invites/{invite_id}/decline")
async def decline_pending_invite(
    invite_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    invite = _invite_for_user(invite_id, user_id, pending_only=False)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.get("status") != "pending":
        _raise_invalid_invite_status(invite)
    if _is_past_timestamp(invite.get("expires_at")):
        _set_invite_status(invite_id, status="expired")
        _mark_invite_notifications_read(sb, invite_id, user_id)
        raise HTTPException(status_code=410, detail="Invite has expired")
    _set_invite_status(invite_id, status="declined")
    _mark_invite_notifications_read(sb, invite_id, user_id)
    _publish_list_sync_event(invite["list_id"], "invites_updated", "invite_declined")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{list_id}")
async def get_list(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    return _list_detail(sb, list_id, user_id)


@router.get("/{list_id}/events")
async def stream_list_events(
    list_id: str,
    request: Request,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> StreamingResponse:
    if not has_direct_postgres():
        raise HTTPException(
            status_code=503,
            detail="List realtime sync requires direct Postgres access",
        )
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)

    async def event_stream():
        connection = connect_listener()
        try:
            yield _format_sse_message(
                "keepalive",
                {"list_id": list_id, "changed_at": _now_utc(), "reason": "connected"},
                event_id=str(time.time_ns()),
            )
            while True:
                if await request.is_disconnected():
                    break
                next_event = await asyncio.to_thread(
                    wait_for_list_sync_event,
                    connection,
                    timeout_seconds=LIST_SYNC_HEARTBEAT_SECONDS,
                )
                if next_event is None:
                    yield _format_sse_message(
                        "keepalive",
                        {
                            "list_id": list_id,
                            "changed_at": _now_utc(),
                            "reason": "heartbeat",
                        },
                        event_id=str(time.time_ns()),
                    )
                    continue
                if next_event["list_id"] != list_id:
                    continue
                yield _format_sse_message(
                    next_event["event"],
                    {
                        "list_id": next_event["list_id"],
                        "changed_at": next_event["changed_at"],
                        "reason": next_event["reason"],
                    },
                    event_id=next_event["id"],
                )
        finally:
            connection.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{list_id}/reset")
async def reset_list(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    sb.table("shopping_lists").update({"items": []}).eq("id", list_id).execute()
    _publish_list_sync_event(list_id, "list_updated", "list_reset")
    row = (
        sb.table("shopping_lists")
        .select("*")
        .eq("id", list_id)
        .single()
        .execute()
        .data
    )
    row = dict(row)
    row["items"] = _project_list_items_for_viewer(
        sb,
        _enrich_items_with_categories(sb, row.get("items") or []),
        user_id,
    )
    return row


@router.post("/{list_id}/items", status_code=status.HTTP_201_CREATED)
async def add_item(
    list_id: str,
    body: AddItemBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
    access_token: Annotated[str, Depends(get_current_access_token)],
) -> dict:
    import uuid

    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    new_item = {
        "id": str(uuid.uuid4()),
        "name": body.name,
        "brand": body.brand,
        "quantity": body.quantity,
        "unit": body.unit,
        "checked": False,
        "checked_by": None,
        "checked_at": None,
        "added_by": user_id,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "source": body.source,
        "pinned_offer_id": body.pinned_offer_id,
        "image_url": body.image_url,
        "category": None,
        "subcategory": None,
        "found_deals": [],
    }
    if body.pinned_offer_id:
        try:
            offer_patch = _selected_offer_patch(sb, body.pinned_offer_id)
            new_item.update(offer_patch)
        except HTTPException:
            pass
    new_item = _enrich_items_with_categories(sb, [new_item])[0]
    await _rpc_append_list_item(list_id, new_item, user_id, access_token)
    _publish_list_sync_event(list_id, "list_updated", "item_added")
    return project_item_for_viewer(
        new_item,
        _hidden_offer_ids_for_viewer(sb, [new_item], user_id),
    )


@router.post("/{list_id}/items/remove-purchased")
async def remove_purchased_items(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    current = (
        sb.table("shopping_lists")
        .select("items")
        .eq("id", list_id)
        .single()
        .execute()
    )
    items = current.data["items"] or []
    remaining_items = [item for item in items if not item.get("purchased")]
    sb.table("shopping_lists").update({"items": remaining_items}).eq("id", list_id).execute()
    _publish_list_sync_event(list_id, "list_updated", "purchased_items_removed")
    row = (
        sb.table("shopping_lists")
        .select("*")
        .eq("id", list_id)
        .single()
        .execute()
        .data
    )
    row = dict(row)
    row["items"] = _project_list_items_for_viewer(
        sb,
        _enrich_items_with_categories(sb, row.get("items") or []),
        user_id,
    )
    return row


@router.delete("/{list_id}/items/{item_id}")
async def remove_item(
    list_id: str,
    item_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    access_token: Annotated[str, Depends(get_current_access_token)],
) -> Response:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    current = sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    items = current.data["items"] or []
    target = next((i for i in items if i["id"] == item_id), None)
    if target and target.get("purchased"):
        raise HTTPException(status_code=409, detail="Cannot remove a purchased item; undo purchase first")
    if target and target.get("purchased_by"):
        sb.table("purchase_history").delete().eq("list_item_id", item_id).eq("user_id", target["purchased_by"]).execute()
    await _rpc_remove_list_item(list_id, item_id, user_id, access_token)
    _publish_list_sync_event(list_id, "list_updated", "item_removed")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{list_id}/items/{item_id}/toggle")
async def toggle_item(
    list_id: str,
    item_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    access_token: Annotated[str, Depends(get_current_access_token)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    current = sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    items = current.data["items"]
    toggled = _find_item(items, item_id)
    new_checked = not toggled.get("checked", False)
    patch = {
        "checked": new_checked,
        "checked_by": user_id if new_checked else None,
        "checked_at": _now_utc() if new_checked else None,
    }
    await _rpc_update_list_item(list_id, item_id, patch, user_id, access_token)
    _publish_list_sync_event(list_id, "list_updated", "item_toggled")
    toggled = {**toggled, **patch}
    if toggled is None:
        raise HTTPException(status_code=404, detail="Item not found")
    toggled = _enrich_items_with_categories(sb, [toggled])[0]
    return project_item_for_viewer(
        toggled,
        _hidden_offer_ids_for_viewer(sb, [toggled], user_id),
    )


@router.post("/{list_id}/items/{item_id}/check")
async def set_item_checked(
    list_id: str,
    item_id: str,
    body: dict,
    user_id: Annotated[str, Depends(get_current_user_id)],
    access_token: Annotated[str, Depends(get_current_access_token)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    current = sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    item = _find_item(current.data["items"], item_id)
    checked = bool(body.get("checked"))
    patch = {
        "checked": checked,
        "checked_by": user_id if checked else None,
        "checked_at": _now_utc() if checked else None,
    }
    await _rpc_update_list_item(list_id, item_id, patch, user_id, access_token)
    _publish_list_sync_event(list_id, "list_updated", "item_checked")
    checked_item = _enrich_items_with_categories(sb, [{**item, **patch}])[0]
    return project_item_for_viewer(
        checked_item,
        _hidden_offer_ids_for_viewer(sb, [checked_item], user_id),
    )


@router.patch("/{list_id}/items/{item_id}")
async def patch_item(
    list_id: str,
    item_id: str,
    body: UpdateListItemBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
    access_token: Annotated[str, Depends(get_current_access_token)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    current = sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    items = current.data["items"]
    patch = body.model_dump(exclude_none=True)
    current_item = _find_item(items, item_id)
    patch.update(_validated_category_patch(body, current_item))
    if body.pinned_offer_id:
        patch.update(_selected_offer_patch(sb, body.pinned_offer_id))
    _patch_item_in_items(items, item_id, patch)
    await _rpc_update_list_item(list_id, item_id, patch, user_id, access_token)
    _publish_list_sync_event(list_id, "list_updated", "item_patched")
    refreshed = (
        sb.table("shopping_lists")
        .select("items")
        .eq("id", list_id)
        .single()
        .execute()
        .data["items"]
    )
    refreshed_item = _enrich_items_with_categories(
        sb, [_find_item(refreshed, item_id)]
    )[0]
    return project_item_for_viewer(
        refreshed_item,
        _hidden_offer_ids_for_viewer(sb, [refreshed_item], user_id),
    )


@router.post("/{list_id}/invites", status_code=status.HTTP_201_CREATED)
async def invite_member_by_email(
    list_id: str,
    body: InviteByEmailBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_owner(sb, list_id, user_id)
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required")
    invited_user = _auth_user_by_email(email)
    if invited_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    invited_user_id = invited_user["id"]
    if invited_user_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot invite yourself")
    if _existing_member(list_id, invited_user_id):
        raise HTTPException(status_code=409, detail="User is already a member")
    if _pending_invite_for_target(list_id, invited_user_id):
        raise HTTPException(status_code=409, detail="Pending invite already exists")
    list_row = _shopping_list_row(list_id)
    inviter_profile = _profile_row(sb, user_id)
    inviter_name = inviter_profile.get("display_name") or "Un utente"
    invite = _insert_list_invite(list_id, user_id, invited_user_id, email)
    title = "Invito lista spesa"
    body_text = f"{inviter_name} ti ha invitato a condividere la sua lista"
    payload = _invite_payload(list_row["name"], inviter_name, invite["id"], list_id)
    notification = _notify_shared_list_event(
        sb,
        invited_user_id,
        kind="list_invite",
        title=title,
        body=body_text,
        data=payload,
    )
    _publish_list_sync_event(list_id, "invites_updated", "invite_created")
    invite["notification"] = notification
    return invite


@router.get("/{list_id}/invites")
async def list_list_invites(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> list[dict]:
    sb = get_supabase()
    _verify_owner(sb, list_id, user_id)
    invites = (
        sb.table("list_invites")
        .select("*")
        .eq("list_id", list_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    inviter_ids = sorted({invite["invited_by"] for invite in invites})
    invited_ids = sorted({invite["invited_user_id"] for invite in invites if invite.get("invited_user_id")})
    profile_ids = sorted(set(inviter_ids + invited_ids))
    profiles = (
        sb.table("user_profiles")
        .select("id, display_name")
        .in_("id", profile_ids)
        .execute()
        .data
        if profile_ids
        else []
    )
    names = {profile["id"]: profile.get("display_name") for profile in profiles}
    for invite in invites:
        invite["invited_by_name"] = names.get(invite["invited_by"])
        invite["invited_user_name"] = names.get(invite.get("invited_user_id"))
    return invites


@router.delete("/{list_id}/invites/{invite_id}")
async def revoke_invite(
    list_id: str,
    invite_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    _verify_owner(sb, list_id, user_id)
    invite_resp = (
        sb.table("list_invites")
        .select("invited_user_id, status")
        .eq("id", invite_id)
        .eq("list_id", list_id)
        .limit(1)
        .execute()
    )
    if not invite_resp.data:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite = invite_resp.data[0]
    if invite["status"] == "pending":
        revoked_at = _now_utc()
        list_name = _shopping_list_row(list_id).get("name") or "questa lista"
        inviter_name = _profile_row(sb, user_id).get("display_name")
        body = (
            f'L\'invito alla lista "{list_name}" e\' stato revocato'
            + (f" da {inviter_name}." if inviter_name else ".")
        )
        (
            sb.table("list_invites")
            .update({"status": "revoked"})
            .eq("id", invite_id)
            .execute()
        )
        if invite.get("invited_user_id"):
            _update_invite_notifications(
                sb,
                invite_id,
                invite["invited_user_id"],
                title="Invito revocato",
                body=body,
                data={
                    "invite_id": invite_id,
                    "invite_status": "revoked",
                    "revoked_at": revoked_at,
                    "list_id": list_id,
                    "list_name": list_name,
                    "invited_by": inviter_name,
                    "url": "/lista",
                },
            )
        _publish_list_sync_event(list_id, "invites_updated", "invite_revoked")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
        profile = profiles_by_id.get(m["user_id"]) or {}
        email = None
        try:
            user_resp = sb.auth.admin.get_user_by_id(m["user_id"])
            email = getattr(user_resp.user, "email", None)
        except Exception:
            email = None
        m["display_name"] = profile.get("display_name")
        m["avatar_url"] = profile.get("avatar_url")
        m["email"] = email
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
    hidden_offer_ids = _hidden_offer_ids_for_viewer(sb, items, user_id)

    offer_ids = [
        item["pinned_offer_id"]
        for item in items
        if item.get("pinned_offer_id") and item["pinned_offer_id"] not in hidden_offer_ids
    ]

    offers_by_id: dict[str, dict] = {}
    if offer_ids:
        rows = (
            sb.table("offers")
            .select("id, price_offer, valid_from, valid_to, is_active")
            .in_("id", offer_ids)
            .execute()
        ).data
        offers_by_id = {row["id"]: row for row in rows}

    return [
        {
            "item_id": entry["list_item_id"],
            "item_name": entry["list_item_name"],
            "staleness": entry["status"],
            "current_price": entry["current_price"],
            "snapshot_price": entry["pinned_price"],
            "pinned_offer_id": entry.get("pinned_offer_id"),
            "offer_visibility_status": (
                HIDDEN_FOR_VIEWER
                if entry.get("pinned_offer_id") in hidden_offer_ids
                else None
            ),
        }
        for entry in classify_deal_freshness(items, offers_by_id)
    ]


@router.post("/{list_id}/clear-stale-offers")
async def clear_stale_offers(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    access_token: Annotated[str, Depends(get_current_access_token)],
) -> dict:
    """Clear pinned_offer_id and found_deals for expired or unavailable items."""
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)

    list_row = (
        sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    )
    items: list[dict] = list_row.data.get("items") or []
    hidden_offer_ids = _hidden_offer_ids_for_viewer(sb, items, user_id)

    offer_ids = [
        item["pinned_offer_id"]
        for item in items
        if item.get("pinned_offer_id") and item["pinned_offer_id"] not in hidden_offer_ids
    ]
    offers_by_id: dict[str, dict] = {}
    if offer_ids:
        rows = (
            sb.table("offers")
            .select("id, price_offer, valid_from, valid_to, is_active")
            .in_("id", offer_ids)
            .execute()
        ).data
        offers_by_id = {row["id"]: row for row in rows}

    freshness = classify_deal_freshness(items, offers_by_id)
    stale = [
        entry
        for entry in freshness
        if entry["status"] in ("expired", "unavailable")
        and entry.get("pinned_offer_id") not in hidden_offer_ids
    ]

    cleared_names: list[str] = []
    for entry in stale:
        await _rpc_update_list_item(
            list_id,
            entry["list_item_id"],
            {"pinned_offer_id": None, "found_deals": []},
            user_id,
            access_token,
        )
        cleared_names.append(entry["list_item_name"])

    if stale:
        _publish_list_sync_event(list_id, "list_updated", "stale_offers_cleared")
    return {"cleared": len(stale), "cleared_names": cleared_names}


@router.delete("/{list_id}/members/{member_user_id}")
async def remove_member(
    list_id: str,
    member_user_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    if not _existing_member(list_id, member_user_id):
        raise HTTPException(status_code=404, detail="Member not found")

    member_role = _list_member_role(sb, list_id, user_id)
    if member_role is None:
        raise HTTPException(status_code=403, detail="Not a member of this list")

    is_self_leave = member_user_id == user_id
    if is_self_leave:
        if member_role != "member":
            raise HTTPException(status_code=400, detail="Owner cannot remove themselves")
    else:
        _verify_owner(sb, list_id, user_id)

    list_row = _shopping_list_row(list_id)
    _delete_member(list_id, member_user_id)
    _fallback_selected_list_for_users(sb, {member_user_id}, list_id)

    if is_self_leave:
        owner_id = list_row.get("user_id")
        if owner_id and owner_id != user_id:
            member_profile = _profile_row(sb, user_id)
            member_auth = _auth_user_by_id(user_id) or {}
            member_name = member_profile.get("display_name")
            member_email = member_auth.get("email")
            member_label = _format_notification_actor(member_name, member_email)
            title = "Membro uscito dalla lista"
            body = f"{member_label} ha lasciato la lista {list_row['name']}"
            payload = {
                **_list_member_left_payload(list_row["name"], member_name, list_id),
                "left_by_email": member_email,
            }
            _notify_shared_list_event(
                sb,
                owner_id,
                kind="list_member_left",
                title=title,
                body=body,
                data=payload,
            )
        _publish_list_sync_event(list_id, "members_updated", "member_left")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    owner_profile = _profile_row(sb, user_id)
    owner_auth = _auth_user_by_id(user_id) or {}
    owner_name = owner_profile.get("display_name")
    owner_email = owner_auth.get("email")
    owner_label = _format_notification_actor(owner_name, owner_email)
    title = "Rimosso dalla lista"
    body = f"{owner_label} ti ha rimosso dalla lista {list_row['name']}"
    payload = {
        **_list_member_removed_payload(list_row["name"], owner_name, list_id),
        "removed_by_email": owner_email,
    }
    _notify_shared_list_event(
        sb,
        member_user_id,
        kind="list_member_removed",
        title=title,
        body=body,
        data=payload,
    )
    _publish_list_sync_event(list_id, "members_updated", "member_removed")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
