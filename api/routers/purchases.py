from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.database import get_supabase

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
    product_id: str | None
    offer_id: str | None
    supermarket_id: str | None
    supermarket_name: str | None
    quantity: float
    price_paid: float
    price_original: float | None
    discount_pct: int | None
    savings: float
    purchased_at: str


class SavingsSummary(BaseModel):
    total_savings: float
    total_purchases: int
    period_days: int
    records: list[PurchaseRecord]


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
        offer_resp = (
            sb.table("offers")
            .select("id, product_id, price_offer, price_original, discount_pct, supermarket_id, supermarkets(name)")
            .eq("id", offer_id)
            .maybe_single()
            .execute()
        )
        if offer_resp and offer_resp.data:
            offer_data = offer_resp.data

    if not offer_data and item.get("found_deals"):
        deal = item["found_deals"][0]
        offer_data = {
            "id": None,
            "product_id": deal.get("product_id"),
            "price_offer": deal.get("price_offer"),
            "price_original": deal.get("price_original"),
            "discount_pct": deal.get("discount_pct"),
            "supermarket_id": deal.get("supermarket_id"),
            "supermarkets": {"name": deal.get("supermarket_name")},
        }

    quantity = float(item.get("quantity") or 1)
    unit_price_paid = float(offer_data.get("price_offer") or 0)
    price_paid = round(unit_price_paid * quantity, 2)
    price_original = (
        round(float(offer_data["price_original"]) * quantity, 2)
        if offer_data.get("price_original")
        else None
    )

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
    }

    result = sb.table("purchase_history").insert(record_insert).execute()
    record = result.data[0]

    updated_items = [
        {
            **i,
            "purchased": True,
            "purchased_by": user_id,
            "purchased_at": now,
        }
        if i["id"] == item_id
        else i
        for i in items
    ]
    sb.table("shopping_lists").update({"items": updated_items}).eq("id", body.list_id).execute()

    return PurchaseRecord(
        id=record["id"],
        list_id=record["list_id"],
        list_item_id=record["list_item_id"],
        item_name=record["item_name"],
        product_id=record.get("product_id"),
        offer_id=record.get("offer_id"),
        supermarket_id=record.get("supermarket_id"),
        supermarket_name=record.get("supermarket_name"),
        quantity=float(record.get("quantity") or quantity),
        price_paid=float(record["price_paid"]),
        price_original=float(record["price_original"]) if record.get("price_original") else None,
        discount_pct=record.get("discount_pct"),
        savings=float(record["savings"]) if record.get("savings") else 0.0,
        purchased_at=record["purchased_at"],
    )


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

    updated_items = [
        {**i, "purchased": False, "purchased_by": None, "purchased_at": None}
        if i["id"] == item_id
        else i
        for i in items
    ]
    sb.table("shopping_lists").update({"items": updated_items}).eq("id", list_id).execute()

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

    records = [
        PurchaseRecord(
            id=r["id"],
            list_id=r.get("list_id"),
            list_item_id=r["list_item_id"],
            item_name=r["item_name"],
            product_id=r.get("product_id"),
            offer_id=r.get("offer_id"),
            supermarket_id=r.get("supermarket_id"),
            supermarket_name=r.get("supermarket_name"),
            quantity=float(r.get("quantity") or 1),
            price_paid=float(r["price_paid"]),
            price_original=float(r["price_original"]) if r.get("price_original") else None,
            discount_pct=r.get("discount_pct"),
            savings=float(r["savings"]) if r.get("savings") else 0.0,
            purchased_at=r["purchased_at"],
        )
        for r in records_raw
    ]

    return SavingsSummary(
        total_savings=round(sum(r.savings for r in records), 2),
        total_purchases=len(records),
        period_days=days,
        records=records,
    )
