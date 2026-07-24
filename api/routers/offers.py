"""Manual offer creation — not tied to a flyer."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.auth import managed_supermarket_ids, require_admin_or_manager
from core.database import get_supabase
from services.extraction.normalizer import normalize_unit_price_measure
from services.offer_visibility import apply_current_offer_window
from services.product_format import ProductFormat
from api.routers._offer_utils import build_offer_row, insert_and_fetch_offer

router = APIRouter()


@router.get("")
async def list_public_offers(
    q: str | None = Query(None),
    category: str | None = Query(None),
    supermarket_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    """Return currently visible offers; offer fields are self-contained."""
    sb = get_supabase()
    query = (
        sb.table("offers")
        .select("*, supermarkets(name, slug, logo_url, address, city)", count="exact")
        .eq("is_confirmed", True)
        .eq("offer_kind", "published_target")
    )
    query = apply_current_offer_window(query)
    if q:
        query = query.ilike("name", f"%{q.strip()}%")
    if category:
        query = query.eq("category", category)
    if supermarket_id:
        query = query.eq("supermarket_id", supermarket_id)
    response = query.order("name").range(offset, offset + limit - 1).execute()
    items = []
    for row in response.data or []:
        supermarket = row.pop("supermarkets", None) or {}
        items.append({
            **row,
            "supermarket_name": supermarket.get("name") or row.get("supermarket_name"),
            "supermarket_slug": supermarket.get("slug"),
            "supermarket_logo_url": supermarket.get("logo_url"),
            "supermarket_address": ", ".join(
                value for value in (supermarket.get("address"), supermarket.get("city")) if value
            ) or None,
        })
    total = response.count or 0
    return {"items": items, "total": total, "nextPage": offset + limit if offset + limit < total else None}


class ManualOfferCreate(BaseModel):
    supermarket_id: str
    name: str = Field(..., min_length=1)
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    format: ProductFormat = Field(default_factory=ProductFormat)
    price_offer: float = Field(..., gt=0)
    price_original: float | None = Field(None, gt=0)
    unit_price_value: float | None = Field(None, gt=0)
    unit_price_unit: str | None = None
    offer_notes: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_manual_offer(
    payload: ManualOfferCreate,
    profile: Annotated[dict, Depends(require_admin_or_manager)],
) -> dict:
    if profile.get("role") == "supermarket_manager":
        if payload.supermarket_id not in managed_supermarket_ids(profile):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Managers can only create offers for their own supermarket")
    sb = get_supabase()
    sm = sb.table("supermarkets").select("id, name").eq("id", payload.supermarket_id).maybe_single().execute()
    if not sm or not sm.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supermarket not found")
    normalized_unit = normalize_unit_price_measure(payload.unit_price_unit) if payload.unit_price_unit else None
    offer_row = build_offer_row(payload, sm.data["id"], sm.data["name"], None, normalized_unit)
    return insert_and_fetch_offer(sb, offer_row)
