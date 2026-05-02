"""Favorites router — manage per-user product favourites."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from core.auth import get_current_user_id
from core.database import get_supabase
from services.extraction.normalizer import format_unit_price_label

router = APIRouter()


class AddFavoriteBody(BaseModel):
    product_id: str


@router.get("")
async def list_favorites(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> list[dict]:
    """Return all favourited products with their current best active offer (lowest price)."""
    sb = get_supabase()
    favs_resp = (
        sb.table("favorites")
        .select("product_id, products(id, name, brand, format, format_label, image_url, category)")
        .eq("user_id", user_id)
        .execute()
    )
    result = []
    for fav in favs_resp.data:
        product_id: str = fav["product_id"]
        product: dict = fav.get("products") or {}
        offer_resp = (
            sb.table("offers")
            .select(
                "id, price_offer, price_original, discount_pct, valid_to, created_at, "
                "supermarket_name, supermarket_id, unit_price, unit_price_value, unit_price_unit, "
                "supermarkets(logo_url)"
            )
            .eq("product_id", product_id)
            .eq("is_active", True)
            .order("price_offer")
            .limit(1)
            .execute()
        )
        active_offer = offer_resp.data[0] if offer_resp.data else None
        best_offer = None
        if active_offer is not None:
            best_offer = {
                "offer_id": active_offer["id"],
                "supermarket_id": active_offer.get("supermarket_id"),
                "supermarket_name": active_offer.get("supermarket_name"),
                "supermarket_logo_url": (active_offer.get("supermarkets") or {}).get("logo_url"),
                "price_offer": active_offer.get("price_offer"),
                "price_original": active_offer.get("price_original"),
                "discount_pct": active_offer.get("discount_pct"),
                "valid_to": active_offer.get("valid_to"),
                "created_at": active_offer.get("created_at"),
                "unit_price": active_offer.get("unit_price"),
                "unit_price_value": active_offer.get("unit_price_value"),
                "unit_price_unit": active_offer.get("unit_price_unit"),
                "unit_price_label": active_offer.get("unit_price") or format_unit_price_label(
                    active_offer.get("unit_price_value"),
                    active_offer.get("unit_price_unit"),
                ),
            }
        result.append(
            {
                "favorite_id": fav.get("id"),
                "product_id": product_id,
                "name": product.get("name"),
                "brand": product.get("brand"),
                "format": product.get("format"),
                "format_label": product.get("format_label") or "",
                "category": product.get("category"),
                "image_url": product.get("image_url"),
                "best_offer": best_offer,
            }
        )
    return result


@router.get("/{product_id}")
async def check_favorite(
    product_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Return whether the authenticated user has favourited the given canonical product."""
    sb = get_supabase()
    resp = (
        sb.table("favorites")
        .select("id")
        .eq("user_id", user_id)
        .eq("product_id", product_id)
        .execute()
    )
    return {"is_favorite": bool(resp.data)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_favorite(
    body: AddFavoriteBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Add a product to favourites. Idempotent — safe to call if already favourited."""
    sb = get_supabase()
    resp = (
        sb.table("favorites")
        .upsert({"user_id": user_id, "product_id": body.product_id})
        .execute()
    )
    return resp.data[0]


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    product_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> None:
    """Remove a product from favourites. No-op if not currently favourited."""
    sb = get_supabase()
    (
        sb.table("favorites")
        .delete()
        .eq("user_id", user_id)
        .eq("product_id", product_id)
        .execute()
    )
