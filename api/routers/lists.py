from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from jose import jwt
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.config import settings
from core.database import get_postgres_cursor, get_supabase, has_direct_postgres
from services.repositories import lists_repository as repo
from services.extraction.normalizer import format_unit_price_label
from services.deal_freshness import classify_deal_freshness
from services.offer_visibility import apply_current_offer_window
from services.push_notify import (
    PushEndpointGoneError,
    PushSubscription,
    notifications_enabled_for_user,
    send_push_notification,
)

router = APIRouter()
DEFAULT_LIST_NAME = "Lista principale"

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


class CreateListBody(BaseModel):
    name: str = DEFAULT_LIST_NAME


class RenameListBody(BaseModel):
    name: str


class SelectListBody(BaseModel):
    list_id: str


class AddItemBody(BaseModel):
    name: str
    brand: str | None = None
    quantity: float = 1.0
    unit: str | None = None
    source: Literal["manual", "offer"] = "manual"
    pinned_product_id: str | None = None  # canonical products.id (set when source='offer')
    pinned_offer_id: str | None = None    # specific offers.id (set when source='offer')
    image_url: str | None = None


class InviteBody(BaseModel):
    email: str | None = None


class InviteByEmailBody(BaseModel):
    email: str


class UpdateListItemBody(BaseModel):
    quantity: float | None = None
    pinned_offer_id: str | None = None
    category: str | None = None
    subcategory: str | None = None


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_past_timestamp(value: str | datetime | None) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        return value < datetime.now(timezone.utc)
    return value < _now_utc()


def _product_categories(sb: object, product_ids: set[str]) -> dict[str, dict]:
    if not product_ids:
        return {}
    rows = (
        sb.table("products")  # type: ignore[union-attr,attr-defined]
        .select("id, brand, category, subcategory")
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
        .select("id, product_id, products(brand, category, subcategory)")
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
        "brand": category_source.get("brand", item.get("brand")),
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
    product = offer.get("products") or {}
    supermarket = offer.get("supermarkets") or {}
    return {
        "offer_id": offer["id"],
        "product_id": offer["product_id"],
        "product_name": product.get("name"),
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
            "id, product_id, supermarket_id, price_offer, price_original, "
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


def _product_row(sb: object, product_id: str) -> dict:
    rows = (
        sb.table("products")  # type: ignore[union-attr,attr-defined]
        .select("id, name, brand, category, subcategory, image_url")
        .eq("id", product_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else {}


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
    product = _product_row(sb, offer["product_id"])
    supermarket = _supermarket_row(sb, offer.get("supermarket_id"))
    offer = {
        **offer,
        "products": product,
        "supermarkets": supermarket,
    }
    product = offer.get("products") or {}
    return {
        "source": "offer",
        "name": product.get("name", ""),
        "brand": product.get("brand"),
        "pinned_product_id": offer["product_id"],
        "pinned_offer_id": offer["id"],
        "image_url": product.get("image_url"),
        "category": product.get("category"),
        "subcategory": product.get("subcategory"),
        "found_deals": [_deal_snapshot_from_offer(offer)],
    }


def _rpc_token_for_user(user_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": "authenticated",
        "aud": "authenticated",
        "iss": "supabase",
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")


async def _rpc_update_list_item(
    list_id: str,
    item_id: str,
    patch: dict,
    user_id: str,
) -> None:
    await _rpc_call("update_list_item", {
        "p_list_id": list_id,
        "p_item_id": item_id,
        "p_patch": patch,
    }, user_id)


async def _rpc_append_list_item(
    list_id: str,
    item: dict,
    user_id: str,
) -> None:
    await _rpc_call("append_list_item", {
        "p_list_id": list_id,
        "p_item": item,
    }, user_id)


async def _rpc_remove_list_item(
    list_id: str,
    item_id: str,
    user_id: str,
) -> None:
    await _rpc_call("remove_list_item", {
        "p_list_id": list_id,
        "p_item_id": item_id,
    }, user_id)


async def _rpc_call(function_name: str, payload: dict, user_id: str) -> None:
    if has_direct_postgres():
        _direct_rpc_call(function_name, payload, user_id)
        return

    token = _rpc_token_for_user(user_id)
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {token}",
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
                SET items = COALESCE(items, '[]'::jsonb) || jsonb_build_array(%s::jsonb),
                    updated_at = now()
                WHERE id = %s
                """,
                (json.dumps(payload["p_item"]), payload["p_list_id"]),
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


def _set_active_list_id(user_id: str, list_id: str | None) -> None:
    repo.set_active_list_id(user_id, list_id)


def _default_list_id_for_user(sb: object, user_id: str) -> str | None:
    return repo.default_list_id_for_user(sb, user_id)


def _is_default_by_list_id(list_id: str) -> bool:
    return repo.is_default_by_list_id(list_id)


def _list_default_flags(list_ids: list[str]) -> dict[str, bool]:
    return repo.list_default_flags(list_ids)


def _insert_shopping_list(
    *,
    user_id: str,
    name: str,
    is_default: bool,
    is_active: bool = True,
    items: list[dict] | None = None,
) -> dict:
    return repo.create_owned_list(
        user_id=user_id,
        name=name,
        is_default=is_default,
        is_active=is_active,
        items=items,
    )


def _create_owned_list(
    *,
    user_id: str,
    name: str,
    is_default: bool,
    is_active: bool = True,
    items: list[dict] | None = None,
) -> dict:
    return repo.create_owned_list(
        user_id=user_id,
        name=name,
        is_default=is_default,
        is_active=is_active,
        items=items,
    )


def _shopping_list_row(list_id: str) -> dict:
    return repo.shopping_list_row(list_id)


def _shopping_list_rows(list_ids: list[str]) -> list[dict]:
    return repo.shopping_list_rows(list_ids)


def _rename_shopping_list(list_id: str, name: str) -> None:
    repo.rename_shopping_list(list_id, name)


def _delete_shopping_list(list_id: str) -> None:
    repo.delete_shopping_list(list_id)


def _visible_memberships(sb: object, user_id: str) -> list[dict]:
    return repo.visible_memberships(sb, user_id)


def _resolve_selected_list_id(sb: object, user_id: str) -> str | None:
    profile = _profile_row(sb, user_id)
    memberships = _visible_memberships(sb, user_id)
    visible_ids = {row["list_id"] for row in memberships}
    active_list_id = profile.get("active_list_id")
    if active_list_id and active_list_id in visible_ids:
        return active_list_id
    default_list_id = _default_list_id_for_user(sb, user_id)
    if default_list_id:
        if active_list_id != default_list_id:
            _set_active_list_id(user_id, default_list_id)
        return default_list_id
    if memberships:
        fallback_id = memberships[0]["list_id"]
        _set_active_list_id(user_id, fallback_id)
        return fallback_id
    return None


def _list_member_role(sb: object, list_id: str, user_id: str) -> str | None:
    return repo.list_member_role(sb, list_id, user_id)


def _list_detail(sb: object, list_id: str, user_id: str) -> dict:
    _verify_member(sb, list_id, user_id)
    row = _shopping_list_row(list_id)
    row["items"] = _enrich_items_with_categories(sb, row.get("items") or [])
    row["is_default"] = _is_default_by_list_id(list_id)
    member_role = _list_member_role(sb, list_id, user_id)
    selected_list_id = _resolve_selected_list_id(sb, user_id)
    row["member_role"] = member_role
    row["is_owner"] = member_role == "owner"
    row["is_selected"] = row["id"] == selected_list_id
    return row


def _member_counts(list_ids: list[str]) -> dict[str, int]:
    return repo.member_counts(list_ids)


def _fallback_selected_list_for_users(sb: object, user_ids: set[str], deleted_list_id: str) -> None:
    for impacted_user_id in user_ids:
        default_list_id = _default_list_id_for_user(sb, impacted_user_id)
        if default_list_id is None:
            created = _create_owned_list(
                user_id=impacted_user_id,
                name=DEFAULT_LIST_NAME,
                items=[],
                is_active=True,
                is_default=True,
            )
            default_list_id = created["id"]
        current = _profile_row(sb, impacted_user_id)
        if current.get("active_list_id") in {None, deleted_list_id}:
            _set_active_list_id(impacted_user_id, default_list_id)


def _invite_payload(list_name: str, inviter_name: str | None, invite_id: str, list_id: str) -> dict:
    return {
        "invite_id": invite_id,
        "invite_status": "pending",
        "list_id": list_id,
        "url": f"/lista?invite={invite_id}&list={list_id}",
        "list_name": list_name,
        "invited_by": inviter_name,
    }


def _list_deleted_payload(list_name: str, deleted_by: str | None, list_id: str) -> dict:
    return {
        "list_id": list_id,
        "list_name": list_name,
        "deleted_by": deleted_by,
        "url": "/lista",
    }


def _list_member_removed_payload(list_name: str, removed_by: str | None, list_id: str) -> dict:
    return {
        "list_id": list_id,
        "list_name": list_name,
        "removed_by": removed_by,
        "url": "/lista",
    }


def _list_member_left_payload(list_name: str, left_by: str | None, list_id: str) -> dict:
    return {
        "list_id": list_id,
        "list_name": list_name,
        "left_by": left_by,
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
    if not notifications_enabled_for_user(sb, user_id):
        return None
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


def _impacted_member_user_ids_for_list(list_id: str) -> list[str]:
    return repo.impacted_member_user_ids_for_list(list_id)


def _notify_invited_user(sb: object, user_id: str, title: str, body: str, data: dict) -> None:
    try:
        UUID(user_id)
        use_direct_postgres = has_direct_postgres()
    except ValueError:
        use_direct_postgres = False

    if use_direct_postgres:
        with get_postgres_cursor() as cursor:
            cursor.execute(
                """
                SELECT endpoint, p256dh, auth_key
                FROM public.push_subscriptions
                WHERE user_id = %s
                ORDER BY created_at ASC NULLS LAST, id ASC
                """,
                (user_id,),
            )
            subscriptions = [dict(row) for row in cursor.fetchall()]
    else:
        subs_resp = (
            sb.table("push_subscriptions")  # type: ignore[union-attr,attr-defined]
            .select("endpoint, p256dh, auth_key")
            .eq("user_id", user_id)
            .execute()
        )
        subscriptions = subs_resp.data

    for sub in subscriptions:
        subscription = PushSubscription(
            endpoint=sub["endpoint"],
            p256dh=sub["p256dh"],
            auth_key=sub["auth_key"],
        )
        try:
            send_push_notification(
                subscription=subscription,
                title=title,
                body=body,
                data=data,
            )
        except PushEndpointGoneError:
            (
                sb.table("push_subscriptions")  # type: ignore[union-attr,attr-defined]
                .delete()
                .eq("user_id", user_id)
                .eq("endpoint", sub["endpoint"])
                .execute()
            )
        except Exception:
            continue


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
    memberships = _visible_memberships(sb, user_id)
    if not memberships:
        selected_list = await get_active_list(user_id)
        memberships = [{"list_id": selected_list["id"], "role": "owner"}]
    list_ids = [membership["list_id"] for membership in memberships]
    role_by_list_id = {membership["list_id"]: membership["role"] for membership in memberships}
    selected_list_id = _resolve_selected_list_id(sb, user_id)
    list_rows = _shopping_list_rows(list_ids)
    default_flags = _list_default_flags(list_ids)
    member_count_by_list = _member_counts(list_ids)
    owner_ids = sorted({row["user_id"] for row in list_rows if row.get("user_id")})
    owner_profiles = (
        sb.table("user_profiles")
        .select("id, display_name")
        .in_("id", owner_ids)
        .execute()
        .data
        if owner_ids
        else []
    )
    owner_names = {profile["id"]: profile.get("display_name") for profile in owner_profiles}
    summaries: list[dict] = []
    for row in list_rows:
        role = role_by_list_id.get(row["id"])
        summaries.append({
            "id": row["id"],
            "user_id": row.get("user_id"),
            "name": row.get("name"),
            "is_active": row.get("is_active", True),
            "is_default": default_flags.get(row["id"], False),
            "is_selected": row["id"] == selected_list_id,
            "member_role": role,
            "is_owner": role == "owner",
            "member_count": member_count_by_list.get(row["id"], 0),
            "item_count": len(row.get("items") or []),
            "owner_display_name": owner_names.get(row.get("user_id")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        })
    return summaries


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_list(
    body: CreateListBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    name = body.name.strip() or "Nuova lista"
    created = _create_owned_list(
        user_id=user_id,
        name=name,
        items=[],
        is_active=True,
        is_default=False,
    )
    _set_active_list_id(user_id, created["id"])
    return _list_detail(sb, created["id"], user_id)


@router.post("/select")
async def select_list(
    body: SelectListBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, body.list_id, user_id)
    _set_active_list_id(user_id, body.list_id)
    return _list_detail(sb, body.list_id, user_id)


@router.get("/active")
async def get_active_list(user_id: Annotated[str, Depends(get_current_user_id)]) -> dict:
    """Return currently selected shopping list. Creates default one if missing."""
    sb = get_supabase()
    selected_list_id = _resolve_selected_list_id(sb, user_id)
    if selected_list_id:
        return _list_detail(sb, selected_list_id, user_id)

    new_list = _create_owned_list(
        user_id=user_id,
        name=DEFAULT_LIST_NAME,
        items=[],
        is_active=True,
        is_default=True,
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
    _set_invite_status(invite_id, status="accepted", accepted_by=user_id)
    _mark_invite_notifications_read(sb, invite_id, user_id)
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{list_id}")
async def get_list(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    return _list_detail(sb, list_id, user_id)


@router.patch("/{list_id}")
async def rename_list(
    list_id: str,
    body: RenameListBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_owner(sb, list_id, user_id)
    if _is_default_by_list_id(list_id):
        raise HTTPException(status_code=400, detail="Default list cannot be renamed")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="List name is required")
    _rename_shopping_list(list_id, name)
    return _list_detail(sb, list_id, user_id)


@router.delete("/{list_id}")
async def delete_list(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    sb = get_supabase()
    _verify_owner(sb, list_id, user_id)
    if _is_default_by_list_id(list_id):
        raise HTTPException(status_code=400, detail="Default list cannot be deleted")
    list_row = _shopping_list_row(list_id)
    owner_profile = _profile_row(sb, user_id)
    owner_name = owner_profile.get("display_name") or "Un utente"
    member_user_ids = _impacted_member_user_ids_for_list(list_id)
    impacted_users = repo.impacted_user_ids_for_list(list_id)
    _delete_shopping_list(list_id)
    _fallback_selected_list_for_users(sb, impacted_users, list_id)
    title = "Lista rimossa"
    body = f"{owner_name} ha rimosso la lista {list_row['name']}"
    payload = _list_deleted_payload(list_row["name"], owner_name, list_id)
    for member_user_id in member_user_ids:
        _notify_shared_list_event(
            sb,
            member_user_id,
            kind="list_deleted",
            title=title,
            body=body,
            data=payload,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{list_id}/reset")
async def reset_list(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)
    sb.table("shopping_lists").update({"items": []}).eq("id", list_id).execute()
    row = (
        sb.table("shopping_lists")
        .select("*")
        .eq("id", list_id)
        .single()
        .execute()
        .data
    )
    return row


@router.post("/{list_id}/items", status_code=status.HTTP_201_CREATED)
async def add_item(
    list_id: str,
    body: AddItemBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
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
        "pinned_product_id": body.pinned_product_id,
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
    await _rpc_append_list_item(list_id, new_item, user_id)
    return new_item


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
    row = (
        sb.table("shopping_lists")
        .select("*")
        .eq("id", list_id)
        .single()
        .execute()
        .data
    )
    return row


@router.delete("/{list_id}/items/{item_id}")
async def remove_item(
    list_id: str,
    item_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
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
    await _rpc_remove_list_item(list_id, item_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{list_id}/items/{item_id}/toggle")
async def toggle_item(
    list_id: str,
    item_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
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
    await _rpc_update_list_item(list_id, item_id, patch, user_id)
    toggled = {**toggled, **patch}
    if toggled is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return toggled


@router.post("/{list_id}/items/{item_id}/check")
async def set_item_checked(
    list_id: str,
    item_id: str,
    body: dict,
    user_id: Annotated[str, Depends(get_current_user_id)],
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
    await _rpc_update_list_item(list_id, item_id, patch, user_id)
    return {**item, **patch}


@router.patch("/{list_id}/items/{item_id}")
async def patch_item(
    list_id: str,
    item_id: str,
    body: UpdateListItemBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
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
    await _rpc_update_list_item(list_id, item_id, patch, user_id)
    refreshed = (
        sb.table("shopping_lists")
        .select("items")
        .eq("id", list_id)
        .single()
        .execute()
        .data["items"]
    )
    return _find_item(refreshed, item_id)


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
    body_text = f"{inviter_name} ti ha invitato in {list_row['name']}"
    payload = _invite_payload(list_row["name"], inviter_name, invite["id"], list_id)
    notification = _notify_shared_list_event(
        sb,
        invited_user_id,
        kind="list_invite",
        title=title,
        body=body_text,
        data=payload,
    )
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{list_id}/invite")
async def create_invite(
    list_id: str,
    body: InviteBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    sb = get_supabase()
    _verify_owner(sb, list_id, user_id)

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

    return [
        {
            "item_id": entry["list_item_id"],
            "item_name": entry["list_item_name"],
            "staleness": entry["status"],
            "current_price": entry["current_price"],
            "snapshot_price": entry["pinned_price"],
            "pinned_offer_id": entry.get("pinned_offer_id"),
            "pinned_product_id": entry.get("pinned_product_id"),
        }
        for entry in classify_deal_freshness(items, offers_by_id)
    ]


@router.post("/{list_id}/clear-stale-offers")
async def clear_stale_offers(
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Clear pinned_offer_id and found_deals for expired or unavailable items."""
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)

    list_row = (
        sb.table("shopping_lists").select("items").eq("id", list_id).single().execute()
    )
    items: list[dict] = list_row.data.get("items") or []

    offer_ids = [item["pinned_offer_id"] for item in items if item.get("pinned_offer_id")]
    offers_by_id: dict[str, dict] = {}
    if offer_ids:
        rows = (
            sb.table("offers")
            .select("id, price_offer, valid_to, is_active")
            .in_("id", offer_ids)
            .execute()
        ).data
        offers_by_id = {row["id"]: row for row in rows}

    freshness = classify_deal_freshness(items, offers_by_id)
    stale = [f for f in freshness if f["status"] in ("expired", "unavailable")]

    cleared_names: list[str] = []
    for entry in stale:
        sb.rpc(
            "update_list_item",
            {
                "p_list_id": list_id,
                "p_item_id": entry["list_item_id"],
                "p_updates": {"pinned_offer_id": None, "found_deals": []},
            },
        ).execute()
        cleared_names.append(entry["list_item_name"])

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
            member_name = member_profile.get("display_name") or "Un utente"
            title = "Membro uscito dalla lista"
            body = f"{member_name} ha lasciato la lista {list_row['name']}"
            payload = _list_member_left_payload(list_row["name"], member_name, list_id)
            _notify_shared_list_event(
                sb,
                owner_id,
                kind="list_member_left",
                title=title,
                body=body,
                data=payload,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    owner_profile = _profile_row(sb, user_id)
    owner_name = owner_profile.get("display_name") or "Un utente"
    title = "Rimosso dalla lista"
    body = f"{owner_name} ti ha rimosso dalla lista {list_row['name']}"
    payload = _list_member_removed_payload(list_row["name"], owner_name, list_id)
    _notify_shared_list_event(
        sb,
        member_user_id,
        kind="list_member_removed",
        title=title,
        body=body,
        data=payload,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
