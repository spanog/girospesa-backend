from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.database import get_supabase
from api.routers.lists import _rpc_update_list_item
from services.list_sync import publish_list_sync_event
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
    total_offer_purchases: int
    period_days: int
    records: list[PurchaseRecord]
    next_cursor_purchased_at: str | None = None
    next_cursor_id: str | None = None
    has_more: bool = False


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


def _apply_history_filters(
    query: object,
    *,
    user_id: str,
    cutoff: str,
    category: str | None,
    subcategory: str | None,
    supermarket: str | None,
    source: Literal["all", "offer", "manual"],
) -> object:
    query = (
        query.eq("user_id", user_id)  # type: ignore[union-attr,attr-defined]
        .gte("purchased_at", cutoff)
    )
    if category:
        query = query.eq("category", category)
    if subcategory:
        query = query.eq("subcategory", subcategory)
    if supermarket:
        query = query.eq("supermarket_name", supermarket)
    if source == "offer":
        query = query.not_.is_("offer_id", None)
    elif source == "manual":
        query = query.is_("offer_id", None)
    return query


def _apply_history_cursor(
    query: object,
    *,
    cursor_purchased_at: str | None,
    cursor_id: str | None,
) -> object:
    if not cursor_purchased_at:
        return query
    if cursor_id:
        return query.or_(
            f"purchased_at.lt.{cursor_purchased_at},and(purchased_at.eq.{cursor_purchased_at},id.lt.{cursor_id})"
        )
    return query.lt("purchased_at", cursor_purchased_at)


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
    publish_list_sync_event(body.list_id, "list_updated", "item_purchased")

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
    publish_list_sync_event(list_id, "list_updated", "item_purchase_undone")

    sb.table("purchase_history").delete().eq("list_item_id", item_id).eq("user_id", user_id).execute()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/history")
async def get_history(
    user_id: Annotated[str, Depends(get_current_user_id)],
    days: int = 90,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    category: str | None = None,
    subcategory: str | None = None,
    supermarket: str | None = None,
    source: Literal["all", "offer", "manual"] = "all",
    cursor_purchased_at: str | None = None,
    cursor_id: str | None = None,
) -> SavingsSummary:
    """Return savings history for the authenticated user."""
    sb = get_supabase()
    # PostgREST filters compare literal values, so compute cutoff timestamp here.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    base_query = _apply_history_filters(
        sb.table("purchase_history").select("*"),
        user_id=user_id,
        cutoff=cutoff,
        category=category,
        subcategory=subcategory,
        supermarket=supermarket,
        source=source,
    )
    paged_query = _apply_history_cursor(
        base_query,
        cursor_purchased_at=cursor_purchased_at,
        cursor_id=cursor_id,
    )
    resp = (
        paged_query.order("purchased_at", desc=True)
        .order("id", desc=True)
        .limit(limit + 1)
        .execute()
    )
    records_raw_full: list[dict] = resp.data or []
    has_more = len(records_raw_full) > limit
    records_raw = records_raw_full[:limit]

    summary_rows = (
        _apply_history_filters(
            sb.table("purchase_history").select("price_paid, savings, offer_id"),
            user_id=user_id,
            cutoff=cutoff,
            category=category,
            subcategory=subcategory,
            supermarket=supermarket,
            source=source,
        )
        .execute()
        .data
        or []
    )

    records = [_to_purchase_record(record) for record in records_raw]
    total_purchases = len(summary_rows)
    total_spend = round(
        sum(float(row.get("price_paid") or 0) for row in summary_rows), 2
    )
    total_savings = round(
        sum(float(row.get("savings") or 0) for row in summary_rows), 2
    )
    total_offer_purchases = sum(1 for row in summary_rows if row.get("offer_id"))
    next_cursor_purchased_at = records_raw[-1]["purchased_at"] if has_more and records_raw else None
    next_cursor_id = records_raw[-1]["id"] if has_more and records_raw else None

    return SavingsSummary(
        total_savings=total_savings,
        total_spend=total_spend,
        total_purchases=total_purchases,
        total_offer_purchases=total_offer_purchases,
        period_days=days,
        records=records,
        next_cursor_purchased_at=next_cursor_purchased_at,
        next_cursor_id=next_cursor_id,
        has_more=has_more,
    )
