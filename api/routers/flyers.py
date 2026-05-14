from __future__ import annotations

import uuid
import hashlib
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status

from pydantic import BaseModel, Field

from core.auth import assert_flyer_access, get_current_user_id, get_optional_user_id, require_admin_or_manager
from core.config import settings
from core.database import get_supabase
from services.extraction.normalizer import format_unit_price_label, normalize_unit_price_measure
from services.offer_visibility import apply_current_offer_window
from services.product_format import ProductFormat, build_format_bundle
from api.routers._offer_utils import (
    _OFFER_PRODUCT_SELECT,
    _flatten_draft_offer,
    build_product_row,
    build_format_fields,
    upsert_product,
    build_offer_row,
    insert_and_fetch_offer,
)

router = APIRouter()

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


class DraftOfferUpdate(BaseModel):
    name: str | None = None
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    format: ProductFormat | None = None
    price_offer: float | None = Field(None, gt=0)
    price_original: float | None = Field(None, gt=0)
    unit_price_value: float | None = Field(None, gt=0)
    unit_price_unit: str | None = None
    offer_notes: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


class DraftOfferCreate(BaseModel):
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


def _confirmed_count_by_flyer(sb, flyer_ids: list[str]) -> dict[str, int]:
    if not flyer_ids:
        return {}

    confirmed_resp = apply_current_offer_window(
        sb.table("offers")
        .select("flyer_id")
        .in_("flyer_id", flyer_ids)
        .eq("is_confirmed", True)
    ).execute()
    confirmed_by_flyer: dict[str, int] = {}
    for row in confirmed_resp.data or []:
        fid = row["flyer_id"]
        confirmed_by_flyer[fid] = confirmed_by_flyer.get(fid, 0) + 1
    return confirmed_by_flyer


def _has_confirmed_offers(sb, flyer_id: str) -> bool:
    result = apply_current_offer_window(
        sb.table("offers")
        .select("id", count="exact")
        .eq("flyer_id", flyer_id)
        .eq("is_confirmed", True)
    ).execute()
    return (result.count or 0) > 0


def _normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).strip().lower()
    return normalized or None


def _managed_supermarket_name(sb, managed_id: str | None) -> str | None:
    if not managed_id:
        return None
    result = (
        sb.table("supermarkets")
        .select("name")
        .eq("id", managed_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        return None
    return result.data.get("name")


def _manager_can_access_flyer(sb, profile: dict, flyer: dict) -> bool:
    if profile.get("role") != "supermarket_manager":
        return True

    managed_id = profile.get("managed_supermarket_id")
    if flyer.get("supermarket_id") == managed_id:
        return True

    if flyer.get("supermarket_id") is not None:
        return False

    managed_name = _normalize_name(_managed_supermarket_name(sb, managed_id))
    flyer_name = _normalize_name(flyer.get("supermarket_name"))
    return managed_name is not None and managed_name == flyer_name


def _assert_flyer_access(sb, profile: dict, flyer: dict) -> None:
    if not _manager_can_access_flyer(sb, profile, flyer):
        assert_flyer_access(profile, flyer)


@router.get("")
async def list_flyers(
    admin: bool = Query(False),
    profile: dict = Depends(require_admin_or_manager),
) -> list[dict]:
    """Return flyers for admin/manager. Managers see only their supermarket's flyers."""
    sb = get_supabase()
    query = sb.table("flyers").select("*").order("created_at", desc=True)
    response = query.execute()
    flyers = response.data or []
    if profile.get("role") != "supermarket_manager":
        return flyers
    return [flyer for flyer in flyers if _manager_can_access_flyer(sb, profile, flyer)]


def _nearby_supermarket_ids(sb, lat: float, lng: float, max_distance_km: float) -> list[str]:
    response = sb.rpc(
        "nearby_supermarkets",
        {"user_lat": lat, "user_lng": lng, "radius_m": max_distance_km * 1000},
    ).execute()
    return [row["id"] for row in (response.data or [])]


@router.get("/public")
async def list_public_flyers(
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    max_distance_km: float | None = Query(None, gt=0, le=100),
) -> list[dict]:
    """Return done public flyers that already contain confirmed offers.

    When lat/lng are provided, only flyers from supermarkets within max_distance_km
    (default 10 km) are returned.
    """
    sb = get_supabase()
    query = (
        sb.table("flyers")
        .select("*")
        .eq("status", "done")
        .eq("is_public", True)
        .order("created_at", desc=True)
    )

    if lat is not None and lng is not None:
        radius = max_distance_km if max_distance_km is not None else 10.0
        nearby_ids = _nearby_supermarket_ids(sb, lat, lng, radius)
        if not nearby_ids:
            return []
        query = query.in_("supermarket_id", nearby_ids)

    flyers = query.execute().data
    if not flyers:
        return flyers

    confirmed_by_flyer = _confirmed_count_by_flyer(sb, [f["id"] for f in flyers])
    visible_flyers: list[dict] = []
    for flyer in flyers:
        confirmed_count = confirmed_by_flyer.get(flyer["id"], 0)
        if confirmed_count <= 0:
            continue
        flyer["confirmed_count"] = confirmed_count
        visible_flyers.append(flyer)

    return visible_flyers


_SIGNED_URL_TTL = 60  # seconds


@router.get("/{flyer_id}/download")
async def download_flyer(
    flyer_id: str,
    user_id: str | None = Depends(get_optional_user_id),
) -> dict[str, str]:
    """Generate a short-lived signed download URL for a flyer file.

    Public+done flyers with confirmed offers: accessible to anyone (guests included).
    All other flyers: require a valid JWT with admin or supermarket_manager role.
    """
    sb = get_supabase()
    result = sb.table("flyers").select("*").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")
    flyer = result.data

    is_public_done = (
        flyer.get("is_public")
        and flyer.get("status") == "done"
        and _has_confirmed_offers(sb, flyer_id)
    )

    if not is_public_done:
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Authentication required")
        profile_result = (
            sb.table("user_profiles")
            .select("id, role, managed_supermarket_id")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        if not profile_result or not profile_result.data:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        profile = profile_result.data
        if profile.get("role") not in {"admin", "supermarket_manager"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        assert_flyer_access(profile, flyer)

    prefix = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/flyers/"
    file_url = flyer.get("file_url", "")
    storage_path = file_url.removeprefix(prefix)
    if not storage_path or storage_path == file_url:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cannot resolve flyer storage path")

    signed = sb.storage.from_("flyers").create_signed_url(
        storage_path,
        expires_in=_SIGNED_URL_TTL,
        options={"download": flyer.get("file_name") or True},
    )
    return {"download_url": signed["signedURL"]}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_flyer(
    file: Annotated[UploadFile, File()],
    supermarket_name: str | None = Form(None),
    supermarket_id: str | None = Form(None),
    valid_from: str | None = Form(None),
    valid_to: str | None = Form(None),
    user_id: str = Depends(get_current_user_id),
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Upload a flyer PDF or image. Requires admin or manager role.

    Managers:
    - Auto-fill supermarket_id from managed_supermarket_id if not provided.
    - Cannot upload for a different supermarket.
    """
    role = profile.get("role")
    managed_id = profile.get("managed_supermarket_id")

    if role == "supermarket_manager":
        if supermarket_id is None:
            supermarket_id = managed_id
        elif supermarket_id != managed_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Managers can only upload flyers for their own supermarket",
            )

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type: {file.content_type}",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds 50 MB limit",
        )

    file_hash = hashlib.sha256(content).hexdigest()

    if supermarket_name:
        sb = get_supabase()
        existing = (
            sb.table("flyers")
            .select("id")
            .eq("file_hash", file_hash)
            .eq("supermarket_name", supermarket_name)
            .maybe_single()
            .execute()
        )
        if existing:
            existing_id = existing.data.get("id") if existing.data else None
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Flyer with hash {file_hash} and supermarket '{supermarket_name}' already exists (id={existing_id})",
            )

    sb = get_supabase()
    if supermarket_id and not supermarket_name:
        supermarket_result = (
            sb.table("supermarkets")
            .select("name")
            .eq("id", supermarket_id)
            .maybe_single()
            .execute()
        )
        if supermarket_result and supermarket_result.data:
            supermarket_name = supermarket_result.data.get("name")
    ext = "pdf" if file.content_type == "application/pdf" else "jpg"
    storage_path = f"{user_id}/{uuid.uuid4()}.{ext}"
    sb.storage.from_("flyers").upload(
        path=storage_path,
        file=content,
        file_options={"content-type": file.content_type},
    )

    file_type = "pdf" if file.content_type == "application/pdf" else "image"
    file_url = sb.storage.from_("flyers").get_public_url(storage_path)

    row = (
        sb.table("flyers")
        .insert(
            {
                "user_id": user_id,
                "supermarket_name": supermarket_name,
                "supermarket_id": supermarket_id,
                "file_url": file_url,
                "file_type": file_type,
                "file_name": file.filename,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "status": "pending",
                "is_public": False,
                "file_hash": file_hash,
            }
        )
        .execute()
    )

    return row.data[0]


@router.get("/{flyer_id}")
async def get_flyer(
    flyer_id: str,
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Fetch a single flyer by ID. Managers can only access their supermarket's flyers."""
    sb = get_supabase()
    result = sb.table("flyers").select("*").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")
    flyer = result.data
    _assert_flyer_access(sb, profile, flyer)
    return flyer


@router.delete("/{flyer_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_flyer(
    flyer_id: str,
    profile: dict = Depends(require_admin_or_manager),
) -> None:
    """Delete a flyer immediately: removes storage file (best-effort) and DB row."""
    sb = get_supabase()
    result = sb.table("flyers").select("id, file_url, supermarket_id, supermarket_name").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    flyer = result.data
    _assert_flyer_access(sb, profile, flyer)

    file_url = flyer.get("file_url") or ""
    supabase_prefix = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/flyers/"
    storage_path = file_url.removeprefix(supabase_prefix)
    if storage_path and storage_path != file_url:
        try:
            sb.storage.from_("flyers").remove([storage_path])
        except Exception:
            pass

    sb.table("flyers").delete().eq("id", flyer_id).execute()


@router.post("/{flyer_id}/extract", status_code=status.HTTP_202_ACCEPTED)
async def trigger_extraction(
    flyer_id: str,
    background_tasks: BackgroundTasks,
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Trigger AI extraction for a pending or errored flyer."""
    sb = get_supabase()
    result = sb.table("flyers").select("*").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    flyer = result.data
    _assert_flyer_access(sb, profile, flyer)

    allowed_statuses = {"pending", "error"}
    if flyer.get("status") not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot trigger extraction: flyer status is '{flyer.get('status')}'",
        )

    sb.table("flyers").update({"status": "processing", "error_message": None}).eq("id", flyer_id).execute()

    from services.extraction.service import ExtractionService
    background_tasks.add_task(ExtractionService().run, flyer_id)

    return {"status": "processing", "flyer_id": flyer_id}


@router.get("/{flyer_id}/draft-offers")
async def list_draft_offers(
    flyer_id: str,
    profile: dict = Depends(require_admin_or_manager),
) -> list[dict]:
    """Return all unconfirmed offers for a flyer."""
    sb = get_supabase()
    result = sb.table("flyers").select("id, supermarket_id, supermarket_name").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _assert_flyer_access(sb, profile, result.data)

    offers_resp = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_SELECT)
        .eq("flyer_id", flyer_id)
        .eq("is_confirmed", False)
        .execute()
    )
    return [_flatten_draft_offer(o) for o in (offers_resp.data or [])]


@router.get("/{flyer_id}/confirmed-offers")
async def list_confirmed_offers(
    flyer_id: str,
    profile: dict = Depends(require_admin_or_manager),
) -> list[dict]:
    """Return all confirmed offers for a flyer."""
    sb = get_supabase()
    result = sb.table("flyers").select("id, supermarket_id, supermarket_name").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _assert_flyer_access(sb, profile, result.data)

    offers_resp = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_SELECT)
        .eq("flyer_id", flyer_id)
        .eq("is_confirmed", True)
        .execute()
    )
    return [_flatten_draft_offer(o) for o in (offers_resp.data or [])]


@router.post("/{flyer_id}/draft-offers", status_code=status.HTTP_201_CREATED)
async def create_draft_offer(
    flyer_id: str,
    payload: DraftOfferCreate,
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Manually add a draft offer to a flyer."""
    sb = get_supabase()
    flyer_result = (
        sb.table("flyers")
        .select("id, supermarket_id, supermarket_name, valid_from, valid_to")
        .eq("id", flyer_id)
        .maybe_single()
        .execute()
    )
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")
    flyer = flyer_result.data
    _assert_flyer_access(sb, profile, flyer)

    product_id = upsert_product(sb, build_product_row(payload))
    normalized_unit = normalize_unit_price_measure(payload.unit_price_unit) if payload.unit_price_unit else None
    offer_row = build_offer_row(
        payload, product_id, flyer["supermarket_id"], flyer.get("supermarket_name"),
        flyer_id, normalized_unit, format_fields=build_format_fields(payload),
    )
    # Apply flyer date fallback (flyers.py-specific concern)
    offer_row["valid_from"] = offer_row["valid_from"] or flyer.get("valid_from")
    offer_row["valid_to"] = offer_row["valid_to"] or flyer.get("valid_to")
    return insert_and_fetch_offer(sb, offer_row)


@router.patch("/{flyer_id}/draft-offers/{offer_id}")
async def update_draft_offer(
    flyer_id: str,
    offer_id: str,
    payload: DraftOfferUpdate,
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Inline-edit a single draft offer (and its canonical product if needed)."""
    sb = get_supabase()
    flyer_result = sb.table("flyers").select("id, supermarket_id, supermarket_name").eq("id", flyer_id).maybe_single().execute()
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _assert_flyer_access(sb, profile, flyer_result.data)

    offer_result = (
        sb.table("offers")
        .select("id, product_id, flyer_id, is_confirmed")
        .eq("id", offer_id)
        .eq("flyer_id", flyer_id)
        .maybe_single()
        .execute()
    )
    if not offer_result or not offer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")

    offer = offer_result.data
    sent = payload.model_fields_set

    offer_fields = {
        k: (normalize_unit_price_measure(v) if k == "unit_price_unit" else v)
        for k, v in {
            "price_offer": payload.price_offer,
            "price_original": payload.price_original,
            "unit_price_value": payload.unit_price_value,
            "unit_price_unit": payload.unit_price_unit,
            "offer_notes": payload.offer_notes,
            "valid_from": payload.valid_from,
            "valid_to": payload.valid_to,
        }.items()
        if k in sent
    }
    if "unit_price_value" in offer_fields and "unit_price_unit" in offer_fields:
        offer_fields["unit_price"] = format_unit_price_label(
            offer_fields["unit_price_value"],
            offer_fields["unit_price_unit"],
        )
    if "format" in sent and payload.format is not None:
        format_bundle = build_format_bundle(payload.format.model_dump(mode="json"))
        offer_fields["format"] = format_bundle.format_compact
        offer_fields["format_key"] = format_bundle.format_key
        offer_fields["format_label"] = format_bundle.format_label
    if offer_fields:
        sb.table("offers").update(offer_fields).eq("id", offer_id).execute()

    product_fields = {
        k: v for k, v in {
            "name": payload.name,
            "brand": payload.brand,
            "category": payload.category,
            "subcategory": payload.subcategory,
        }.items()
        if k in sent
    }
    if product_fields:
        sb.table("products").update(product_fields).eq("id", offer["product_id"]).execute()

    updated = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_SELECT)
        .eq("id", offer_id)
        .single()
        .execute()
    )
    return _flatten_draft_offer(updated.data)


@router.delete("/{flyer_id}/draft-offers/{offer_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_draft_offer(
    flyer_id: str,
    offer_id: str,
    profile: dict = Depends(require_admin_or_manager),
) -> None:
    """Delete a single unconfirmed draft offer. Cannot delete already-confirmed offers."""
    sb = get_supabase()
    flyer_result = sb.table("flyers").select("id, supermarket_id, supermarket_name").eq("id", flyer_id).maybe_single().execute()
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _assert_flyer_access(sb, profile, flyer_result.data)

    offer_result = (
        sb.table("offers")
        .select("id, flyer_id, is_confirmed")
        .eq("id", offer_id)
        .eq("flyer_id", flyer_id)
        .maybe_single()
        .execute()
    )
    if not offer_result or not offer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")

    sb.table("offers").delete().eq("id", offer_id).execute()


@router.post("/{flyer_id}/offers/confirm")
async def confirm_offers(
    flyer_id: str,
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Confirm all draft offers for a flyer (sets is_confirmed=True)."""
    sb = get_supabase()
    flyer_result = sb.table("flyers").select("*").eq("id", flyer_id).maybe_single().execute()
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    flyer = flyer_result.data
    _assert_flyer_access(sb, profile, flyer)

    if flyer.get("status") != "done":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot confirm offers: flyer status is '{flyer.get('status')}' (must be 'done')",
        )

    updated = (
        sb.table("offers")
        .update({"is_confirmed": True}, returning="representation")
        .eq("flyer_id", flyer_id)
        .eq("is_confirmed", False)
        .execute()
    )
    confirmed_count = len(updated.data) if updated.data else 0

    total_confirmed = (
        sb.table("offers")
        .select("id", count="exact")
        .eq("flyer_id", flyer_id)
        .eq("is_confirmed", True)
        .execute()
    )
    if (total_confirmed.count or 0) > 0:
        sb.table("flyers").update({"is_public": True}).eq("id", flyer_id).execute()

    return {"confirmed": confirmed_count, "flyer_id": flyer_id}


@router.post("/admin/cleanup", status_code=200)
async def trigger_flyer_cleanup(
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Manually trigger expired-flyer cleanup. Admin only. For ops and testing."""
    if profile.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    from services.flyer_cleanup import FlyerCleanupService
    deleted = FlyerCleanupService().run()
    return {"deleted": deleted}
