"""Shared helpers for product upsert + offer insert, used by flyers and offers routers."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status

from core.database import get_supabase  # noqa: F401 — re-exported for callers
from services.extraction.normalizer import format_unit_price_label
from services.product_format import build_format_bundle

_OFFER_PRODUCT_SELECT = (
    "*, products(id, name, brand, category, subcategory, image_url)"
)


def draft_product_key(name: str | None, brand: str | None) -> str:
    normalized_name = " ".join((name or "").split()).strip().lower()
    normalized_brand = " ".join((brand or "").split()).strip().lower()
    return f"{normalized_name}|{normalized_brand}"


def _flatten_draft_offer(offer: dict) -> dict:
    offer = dict(offer)
    product = offer.pop("products") or {}
    draft_image_url = offer.get("draft_image_url")
    linked_product = None
    if product:
        linked_product = {
            "id": product.get("id"),
            "name": product.get("name", ""),
            "brand": product.get("brand"),
            "category": product.get("category"),
            "subcategory": product.get("subcategory"),
            "image_url": product.get("image_url"),
        }
    name = offer.get("draft_name") or product.get("name", "")
    brand = offer.get("draft_brand") if "draft_brand" in offer else product.get("brand")
    category = offer.get("draft_category") if "draft_category" in offer else product.get("category")
    subcategory = offer.get("draft_subcategory") if "draft_subcategory" in offer else product.get("subcategory")
    return {
        **offer,  # includes format, format_key, format_label from offers table
        "name": name,
        "brand": brand,
        "category": category,
        "subcategory": subcategory,
        # Draft review can stage an image before the canonical product exists.
        "image_url": draft_image_url or product.get("image_url"),
        "linked_product": linked_product,
        "binding_status": "existing" if linked_product else "new_on_confirm",
        "unit_price_label": offer.get("unit_price") or format_unit_price_label(
            offer.get("unit_price_value"),
            offer.get("unit_price_unit"),
        ),
    }


def build_product_row(payload) -> dict:
    """Construct a product dict (no format — format lives in offers)."""
    row = {
        "name": payload.name,
        "brand": payload.brand,
        "category": payload.category,
        "subcategory": payload.subcategory,
    }
    image_url = getattr(payload, "image_url", None)
    if image_url is not None:
        row["image_url"] = image_url
    return row


def build_format_fields(payload) -> dict:
    """Construct format fields from payload for use in offer rows."""
    format_bundle = build_format_bundle(payload.format.model_dump(mode="json"))
    return {
        "format": format_bundle.format_compact,
        "format_key": format_bundle.format_key,
        "format_label": format_bundle.format_label,
    }


def upsert_product(sb, product_row: dict) -> str:
    """Upsert product on conflict (name, brand). Return product_id."""
    upsert_result = sb.table("products").upsert(product_row, on_conflict="name,brand").execute()
    if upsert_result.data:
        return upsert_result.data[0]["id"]
    query = sb.table("products").select("id").eq("name", product_row["name"])
    brand = product_row.get("brand")
    query = query.is_("brand", "null") if brand is None else query.eq("brand", brand)
    existing = query.limit(1).execute()
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to upsert product")
    return existing.data[0]["id"]


def build_offer_row(payload, product_id: str | None, supermarket_id: str, supermarket_name: str | None, flyer_id: str | None, normalized_unit: str | None, format_fields: dict | None = None) -> dict:
    """Build offer dict for insert. valid_from/valid_to from payload only."""
    unit_price_label = format_unit_price_label(payload.unit_price_value, normalized_unit) if payload.unit_price_value else None
    row: dict = {
        "id": str(uuid.uuid4()),
        "product_id": product_id,
        "draft_name": payload.name,
        "draft_brand": payload.brand,
        "draft_category": payload.category,
        "draft_subcategory": payload.subcategory,
        "draft_product_key": draft_product_key(payload.name, payload.brand),
        "flyer_id": flyer_id,
        "supermarket_id": supermarket_id,
        "supermarket_name": supermarket_name,
        "price_offer": payload.price_offer,
        "price_original": payload.price_original,
        "unit_price_value": payload.unit_price_value,
        "unit_price_unit": normalized_unit,
        "unit_price": unit_price_label,
        "offer_notes": payload.offer_notes,
        "valid_from": payload.valid_from,
        "valid_to": payload.valid_to,
        "is_confirmed": False,
    }
    if format_fields:
        row.update(format_fields)
    return row


def insert_and_fetch_offer(sb, offer_row: dict) -> dict:
    """Insert offer row and return flattened offer with product data."""
    insert_result = sb.table("offers").insert(offer_row).execute()
    if not insert_result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create offer")
    fetched = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_SELECT)
        .eq("id", offer_row["id"])
        .single()
        .execute()
    )
    return _flatten_draft_offer(fetched.data)
