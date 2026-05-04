"""Manual offer creation — not tied to a flyer."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.auth import require_admin_or_manager
from core.database import get_supabase
from services.extraction.normalizer import normalize_unit_price_measure
from services.product_format import ProductFormat
from api.routers._offer_utils import build_product_row, upsert_product, build_offer_row, insert_and_fetch_offer

router = APIRouter()


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
        if payload.supermarket_id != profile.get("managed_supermarket_id"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Managers can only create offers for their own supermarket")
    sb = get_supabase()
    sm = sb.table("supermarkets").select("id, name").eq("id", payload.supermarket_id).maybe_single().execute()
    if not sm or not sm.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supermarket not found")
    product_id = upsert_product(sb, build_product_row(payload))
    normalized_unit = normalize_unit_price_measure(payload.unit_price_unit) if payload.unit_price_unit else None
    offer_row = build_offer_row(payload, product_id, sm.data["id"], sm.data["name"], None, normalized_unit)
    return insert_and_fetch_offer(sb, offer_row)
