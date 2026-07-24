"""Shared helpers for self-contained offer creation and review."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from pydantic import ValidationError

from core.database import get_supabase  # noqa: F401 — re-exported for callers
from services.extraction.normalizer import format_unit_price_label
from services.product_format import build_format_bundle

_OFFER_PRODUCT_SELECT = "*"


def draft_product_key(name: str | None, brand: str | None) -> str:
    normalized_name = " ".join((name or "").split()).strip().lower()
    normalized_brand = " ".join((brand or "").split()).strip().lower()
    return f"{normalized_name}|{normalized_brand}"


def _resolved_format_label(offer: dict) -> str:
    label = offer.get("format_label") or ""
    if label or not offer.get("format"):
        return label
    try:
        return build_format_bundle(offer["format"]).format_label
    except (ValueError, ValidationError):
        return label


def _flatten_draft_offer(offer: dict) -> dict:
    offer = dict(offer)
    return {
        **offer,
        "format_label": _resolved_format_label(offer),
        "unit_price_label": offer.get("unit_price") or format_unit_price_label(
            offer.get("unit_price_value"),
            offer.get("unit_price_unit"),
        ),
    }


def build_format_fields(payload) -> dict:
    """Construct format fields from payload for use in offer rows."""
    format_bundle = build_format_bundle(payload.format.model_dump(mode="json"))
    return {
        "format": format_bundle.format_compact,
        "format_key": format_bundle.format_key,
        "format_label": format_bundle.format_label,
    }


def build_offer_row(payload, supermarket_id: str, supermarket_name: str | None, flyer_id: str | None, normalized_unit: str | None, format_fields: dict | None = None) -> dict:
    """Build offer dict for insert. Date fields are optional on payload."""
    unit_price_label = format_unit_price_label(payload.unit_price_value, normalized_unit) if payload.unit_price_value else None
    row: dict = {
        "id": str(uuid.uuid4()),
        "name": payload.name,
        "brand": payload.brand,
        "category": payload.category,
        "subcategory": payload.subcategory,
        "offer_key": draft_product_key(payload.name, payload.brand),
        "flyer_id": flyer_id,
        "supermarket_id": supermarket_id,
        "supermarket_name": supermarket_name,
        "price_offer": payload.price_offer,
        "price_original": payload.price_original,
        "unit_price_value": payload.unit_price_value,
        "unit_price_unit": normalized_unit,
        "unit_price": unit_price_label,
        "offer_notes": payload.offer_notes,
        "valid_from": getattr(payload, "valid_from", None),
        "valid_to": getattr(payload, "valid_to", None),
        "is_confirmed": False,
    }
    if format_fields:
        row.update(format_fields)
    return row


def insert_and_fetch_offer(sb, offer_row: dict) -> dict:
    """Insert offer row and return its normalized offer response."""
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
