from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.database import get_supabase
from api.routers.lists import _rpc_update_list_item
from services.extraction.normalizer import format_unit_price_label

router = APIRouter()


def _verify_member(sb: object, list_id: str, user_id: str) -> None:
    result = (
        sb.table("list_members")  # type: ignore[union-attr,attr-defined]
        .select("id")
        .eq("list_id", list_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=403, detail="Not a member of this list")


class PurchaseItemBody(BaseModel):
    list_id: str
    offer_id: str | None = None


class PurchaseRecord(BaseModel):
    id: str
    list_id: str | None
    list_item_id: str
    item_name: str
    brand: str | None = None
    format_label: str | None = None
    image_url: str | None = None
    category: str | None = None
    subcategory: str | None = None
    product_id: str | None
    offer_id: str | None
    supermarket_id: str | None
    supermarket_name: str | None
    quantity: float
    price_paid: float
    price_original: float | None
    discount_pct: int | None
    unit_price: str | None = None
    unit_price_value: float | None = None
    unit_price_unit: str | None = None
    unit_price_label: str | None = None
    savings: float
    purchased_at: str


class SavingsSummary(BaseModel):
    total_savings: float
    total_spend: float
    total_purchases: int
    period_days: int
    records: list[PurchaseRecord]


def _build_purchase_snapshot(item: dict, offer_data: dict) -> dict:
    deal = (item.get("found_deals") or [None])[0] or {}
    product = offer_data.get("products") or {}
    return {
        "brand": item.get("brand") or product.get("brand"),
        "format_label": deal.get("format_label") or offer_data.get("format_label"),
        "image_url": item.get("image_url") or product.get("image_url"),
        "category": item.get("category") or product.get("category"),
        "subcategory": item.get("subcategory") or product.get("subcategory"),
        "unit_price": deal.get("unit_price") or offer_data.get("unit_price"),
        "unit_price_value": deal.get("unit_price_value") or offer_data.get("unit_price_value"),
        "unit_price_unit": deal.get("unit_price_unit") or offer_data.get("unit_price_unit"),
        "unit_price_label": deal.get("unit_price_label")
        or offer_data.get("unit_price")
        or format_unit_price_label(
            offer_data.get("unit_price_value"),
            offer_data.get("unit_price_unit"),
        ),
    }


def _to_purchase_record(record: dict) -> PurchaseRecord:
    return PurchaseRecord(
        id=record["id"],
        list_id=record.get("list_id"),
        list_item_id=record["list_item_id"],
        item_name=record["item_name"],
        brand=record.get("brand"),
        format_label=record.get("format_label"),
        image_url=record.get("image_url"),
        category=record.get("category"),
        subcategory=record.get("subcategory"),
        product_id=record.get("product_id"),
        offer_id=record.get("offer_id"),
        supermarket_id=record.get("supermarket_id"),
        supermarket_name=record.get("supermarket_name"),
        quantity=float(record.get("quantity") or 1),
        price_paid=float(record["price_paid"]),
        price_original=float(record["price_original"]) if record.get("price_original") else None,
        discount_pct=record.get("discount_pct"),
        unit_price=record.get("unit_price"),
        unit_price_value=float(record["unit_price_value"])
        if record.get("unit_price_value") is not None
        else None,
        unit_price_unit=record.get("unit_price_unit"),
        unit_price_label=record.get("unit_price_label"),
        savings=float(record["savings"]) if record.get("savings") else 0.0,
        purchased_at=record["purchased_at"],
    )


def _purchase_patch(*, purchased: bool, user_id: str | None, at: str | None) -> dict:
    return {
        "purchased": purchased,
        "purchased_by": user_id,
        "purchased_at": at,
    }


def _load_offer_data(sb: object, offer_id: str) -> dict:
    response = (
        sb.table("offers")  # type: ignore[union-attr,attr-defined]
        .select(
            "id, product_id, format_label, price_offer, price_original, "
            "discount_pct, unit_price, unit_price_value, unit_price_unit, "
            "supermarket_id, supermarkets(name), products(brand,image_url,category,subcategory)"
        )
        .eq("id", offer_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else {}


@router.post("/items/{item_id}", status_code=status.HTTP_201_CREATED)
async def purchase_item(
    item_id: str,
    body: PurchaseItemBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> PurchaseRecord:
    """Mark a list item as purchased. Records offer details if available."""
    sb = get_supabase()
    _verify_member(sb, body.list_id, user_id)

    list_row = (
        sb.table("shopping_lists")
        .select("items")
        .eq("id", body.list_id)
        .single()
        .execute()
    )
    items: list[dict] = list_row.data["items"]
    item = next((i for i in items if i["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    offer_id = body.offer_id or item.get("pinned_offer_id")
    offer_data: dict = {}
    if offer_id:
        offer_data = _load_offer_data(sb, offer_id)

    if not offer_data and item.get("found_deals"):
        deal = item["found_deals"][0]
        offer_data = {
            "id": None,
            "product_id": deal.get("product_id"),
            "price_offer": deal.get("price_offer"),
            "price_original": deal.get("price_original"),
            "discount_pct": deal.get("discount_pct"),
            "format_label": deal.get("format_label"),
            "unit_price": deal.get("unit_price"),
            "unit_price_value": deal.get("unit_price_value"),
            "unit_price_unit": deal.get("unit_price_unit"),
            "unit_price_label": deal.get("unit_price_label"),
            "supermarket_id": deal.get("supermarket_id"),
            "supermarkets": {"name": deal.get("supermarket_name")},
            "products": {
                "brand": item.get("brand"),
                "image_url": item.get("image_url"),
                "category": item.get("category"),
                "subcategory": item.get("subcategory"),
            },
        }

    quantity = float(item.get("quantity") or 1)
    unit_price_paid = float(offer_data.get("price_offer") or 0)
    price_paid = round(unit_price_paid * quantity, 2)
    price_original = (
        round(float(offer_data["price_original"]) * quantity, 2)
        if offer_data.get("price_original")
        else None
    )
    snapshot = _build_purchase_snapshot(item, offer_data)

    now = datetime.now(timezone.utc).isoformat()

    record_insert = {
        "user_id": user_id,
        "list_id": body.list_id,
        "list_item_id": item_id,
        "item_name": item["name"],
        "product_id": offer_data.get("product_id"),
        "offer_id": offer_data.get("id"),
        "supermarket_id": offer_data.get("supermarket_id"),
        "supermarket_name": (offer_data.get("supermarkets") or {}).get("name"),
        "quantity": quantity,
        "price_paid": price_paid,
        "price_original": price_original,
        "discount_pct": offer_data.get("discount_pct"),
        **snapshot,
    }

    result = sb.table("purchase_history").insert(record_insert).execute()
    record = result.data[0]

    await _rpc_update_list_item(
        body.list_id,
        item_id,
        _purchase_patch(purchased=True, user_id=user_id, at=now),
        user_id,
    )

    return _to_purchase_record(record)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def undo_purchase(
    item_id: str,
    list_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> Response:
    """Undo a purchase: clears purchased flags and deletes the matching purchase_history row."""
    sb = get_supabase()
    _verify_member(sb, list_id, user_id)

    list_row = (
        sb.table("shopping_lists")
        .select("items")
        .eq("id", list_id)
        .single()
        .execute()
    )
    items: list[dict] = list_row.data["items"]

    if not any(i["id"] == item_id for i in items):
        raise HTTPException(status_code=404, detail="Item not found")

    await _rpc_update_list_item(
        list_id,
        item_id,
        _purchase_patch(purchased=False, user_id=None, at=None),
        user_id,
    )

    sb.table("purchase_history").delete().eq("list_item_id", item_id).eq("user_id", user_id).execute()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/history")
async def get_history(
    user_id: Annotated[str, Depends(get_current_user_id)],
    days: int = 90,
    limit: int = 200,
) -> SavingsSummary:
    """Return savings history for the authenticated user."""
    sb = get_supabase()
    # PostgREST filters compare literal values, so compute cutoff timestamp here.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    resp = (
        sb.table("purchase_history")
        .select("*")
        .eq("user_id", user_id)
        .gte("purchased_at", cutoff)
        .order("purchased_at", desc=True)
        .limit(limit)
        .execute()
    )
    records_raw: list[dict] = resp.data

    records = [_to_purchase_record(record) for record in records_raw]

    return SavingsSummary(
        total_savings=round(sum(r.savings for r in records), 2),
        total_spend=round(sum(r.price_paid for r in records), 2),
        total_purchases=len(records),
        period_days=days,
        records=records,
    )
