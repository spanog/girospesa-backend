"""
Admin-only product management endpoints.
All routes require admin role via require_admin dependency.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from core.auth import require_admin
from core.database import get_supabase
from services.extraction.normalizer import format_unit_price_label, normalize_unit_price_measure

router = APIRouter()

# ── Pydantic schemas ───────────────────────────────────────────────────────────


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    format: str | None = None


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    format: str | None = None


class OfferUpdate(BaseModel):
    price_offer: float | None = None
    price_original: float | None = None
    unit_price_value: float | None = None
    unit_price_unit: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    offer_type: str | None = None
    offer_notes: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_product(product_id: str) -> dict:
    """Fetch a product by id, raise 404 if missing."""
    sb = get_supabase()
    resp = sb.table("products").select("*").eq("id", product_id).single().execute()
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prodotto non trovato")
    return resp.data


def _require_offer(product_id: str, offer_id: str) -> dict:
    """Fetch an offer verifying it belongs to the given product."""
    sb = get_supabase()
    resp = (
        sb.table("offers")
        .select("*, supermarkets(name, logo_url)")
        .eq("id", offer_id)
        .eq("product_id", product_id)
        .single()
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offerta non trovata")
    return resp.data


def _product_has_offers(product_id: str) -> bool:
    """Return True when at least one offer references product."""
    sb = get_supabase()
    resp = sb.table("offers").select("id").eq("product_id", product_id).limit(1).execute()
    return bool(resp.data)


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("")
async def list_products(
    _admin: Annotated[dict, Depends(require_admin)],
    q: str | None = Query(None, description="Full-text or name ILIKE search"),
    category: str | None = Query(None),
    subcategory: str | None = Query(None),
    archived: bool = Query(False, description="Return archived products only"),
    no_image: bool = Query(False, description="Return only products without image"),
    sort_by: str = Query("created_at", description="Column to sort by: name|brand|category|created_at"),
    sort_dir: str = Query("desc", description="Sort direction: asc|desc"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
) -> list[dict]:
    """Paginated list of all products (archived or not). Admin only."""
    sb = get_supabase()

    _SORTABLE = {"name", "brand", "category", "created_at"}
    safe_sort_by = sort_by if sort_by in _SORTABLE else "created_at"
    safe_sort_dir_desc = sort_dir != "asc"

    # Count offers per product via a join
    query = (
        sb.table("products")
        .select("*, offers(id)")
        .eq("is_archived", archived)
        .order(safe_sort_by, desc=safe_sort_dir_desc)
        .range(offset, offset + limit - 1)
    )

    if q:
        query = query.ilike("name", f"%{q}%")

    if category:
        query = query.eq("category", category)

    if subcategory:
        query = query.eq("subcategory", subcategory)

    if no_image:
        query = query.is_("image_url", "null")

    resp = query.execute()
    products = resp.data or []

    # Flatten: replace nested offers list with a count
    for product in products:
        offers = product.pop("offers", None) or []
        product["offers_count"] = len(offers)

    return products


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_product(
    _admin: Annotated[dict, Depends(require_admin)],
    payload: ProductCreate,
) -> dict:
    """Create a canonical product manually. Admin only."""
    sb = get_supabase()

    # Check uniqueness (name, brand, format) — mirrors DB UNIQUE constraint
    exists_q = sb.table("products").select("id").eq("name", payload.name)
    if payload.brand is not None:
        exists_q = exists_q.eq("brand", payload.brand)
    else:
        exists_q = exists_q.is_("brand", "null")
    if payload.format is not None:
        exists_q = exists_q.eq("format", payload.format)
    else:
        exists_q = exists_q.is_("format", "null")

    existing = exists_q.execute()
    if existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un prodotto con questo nome, brand e formato esiste già",
        )

    resp = (
        sb.table("products")
        .insert(
            {
                "id": str(uuid.uuid4()),
                "name": payload.name,
                "brand": payload.brand,
                "category": payload.category,
                "subcategory": payload.subcategory,
                "format": payload.format,
                "is_archived": False,
            }
        )
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=500, detail="Errore durante la creazione del prodotto")
    return resp.data[0]


@router.get("/{product_id}")
async def get_product(
    product_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
) -> dict:
    """Product detail with all its offers. Admin only."""
    product = _require_product(product_id)

    sb = get_supabase()
    offers_resp = (
        sb.table("offers")
        .select("*, supermarkets(name, logo_url)")
        .eq("product_id", product_id)
        .order("created_at", desc=True)
        .execute()
    )

    offers = []
    for o in offers_resp.data or []:
        o = dict(o)
        supermarket = o.pop("supermarkets") or {}
        o["supermarket_name"] = supermarket.get("name", "")
        o["supermarket_logo_url"] = supermarket.get("logo_url")
        o["unit_price_label"] = o.get("unit_price") or format_unit_price_label(
            o.get("unit_price_value"),
            o.get("unit_price_unit"),
        )
        offers.append(o)

    product["offers"] = offers
    return product


@router.patch("/{product_id}")
async def update_product(
    product_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
    payload: ProductUpdate,
) -> dict:
    """Update product fields (partial). Admin only."""
    _require_product(product_id)

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nessun campo da aggiornare")

    sb = get_supabase()
    resp = sb.table("products").update(updates).eq("id", product_id).execute()
    if not resp.data:
        raise HTTPException(status_code=500, detail="Errore durante l'aggiornamento")
    return resp.data[0]


@router.post("/{product_id}/archive")
async def archive_product(
    product_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
) -> dict:
    """Soft-delete a product (is_archived=true). Admin only."""
    _require_product(product_id)

    sb = get_supabase()
    resp = sb.table("products").update({"is_archived": True}).eq("id", product_id).execute()
    if not resp.data:
        raise HTTPException(status_code=500, detail="Errore durante l'archiviazione")
    return resp.data[0]


@router.post("/{product_id}/restore")
async def restore_product(
    product_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
) -> dict:
    """Restore an archived product (is_archived=false). Admin only."""
    _require_product(product_id)

    sb = get_supabase()
    resp = sb.table("products").update({"is_archived": False}).eq("id", product_id).execute()
    if not resp.data:
        raise HTTPException(status_code=500, detail="Errore durante il ripristino")
    return resp.data[0]


@router.delete("/{product_id}")
async def delete_product(
    product_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
) -> dict:
    """Hard-delete archived canonical product when no offers are linked."""
    _require_product(product_id)
    if _product_has_offers(product_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Prodotto con offerte collegate: archivia invece di eliminare.",
        )

    sb = get_supabase()
    sb.table("favorites").delete().eq("product_id", product_id).execute()
    sb.table("products").delete().eq("id", product_id).execute()
    return {"deleted": True}


@router.post("/{product_id}/image")
async def upload_product_image(
    product_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
    file: UploadFile,
) -> dict:
    """
    Upload a product image to Supabase Storage (product-images bucket)
    and update products.image_url. Admin only.
    """
    _require_product(product_id)

    allowed_content_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Formato immagine non supportato. Usa JPEG, PNG, WebP o GIF.",
        )

    ext = (file.filename or "image").rsplit(".", 1)[-1].lower()
    storage_path = f"{product_id}/{uuid.uuid4()}.{ext}"

    content = await file.read()

    sb = get_supabase()
    sb.storage.from_("product-images").upload(
        path=storage_path,
        file=content,
        file_options={"content-type": file.content_type or "image/jpeg", "upsert": "true"},
    )

    public_url = sb.storage.from_("product-images").get_public_url(storage_path)

    resp = (
        sb.table("products")
        .update({"image_url": public_url})
        .eq("id", product_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=500, detail="Errore durante l'aggiornamento dell'immagine")

    return {"image_url": public_url}


@router.patch("/{product_id}/offers/{offer_id}")
async def update_offer(
    product_id: str,
    offer_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
    payload: OfferUpdate,
) -> dict:
    """Edit offer fields. Admin only."""
    _require_offer(product_id, offer_id)

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nessun campo da aggiornare")
    if "unit_price_unit" in updates:
        updates["unit_price_unit"] = normalize_unit_price_measure(updates["unit_price_unit"])
    if "unit_price_value" in updates and updates.get("unit_price_unit"):
        updates["unit_price"] = format_unit_price_label(
            updates["unit_price_value"],
            updates["unit_price_unit"],
        )

    sb = get_supabase()
    resp = sb.table("offers").update(updates).eq("id", offer_id).execute()
    if not resp.data:
        raise HTTPException(status_code=500, detail="Errore durante l'aggiornamento dell'offerta")
    offer = dict(resp.data[0])
    offer["unit_price_label"] = offer.get("unit_price") or format_unit_price_label(
        offer.get("unit_price_value"),
        offer.get("unit_price_unit"),
    )
    return offer


@router.delete("/{product_id}/offers/{offer_id}")
async def delete_offer(
    product_id: str,
    offer_id: str,
    _admin: Annotated[dict, Depends(require_admin)],
) -> dict:
    """Hard-delete an offer. Admin only."""
    _require_offer(product_id, offer_id)

    sb = get_supabase()
    sb.table("offers").delete().eq("id", offer_id).execute()
    return {"deleted": True}
