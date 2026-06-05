from __future__ import annotations

import uuid
import hashlib
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status

from pydantic import BaseModel, Field

from core.auth import (
    assert_flyer_access,
    get_current_user_id,
    get_optional_user_id,
    managed_supermarket_ids,
    require_admin_or_manager,
)
from core.config import settings
from core.database import get_supabase
from services.extraction.normalizer import format_unit_price_label, normalize_unit_price_measure
from services.offer_visibility import apply_current_offer_window
from services.push_notify import notify_public_flyer_published
from services.product_format import ProductFormat, build_format_bundle
from api.routers._offer_utils import (
    _OFFER_PRODUCT_SELECT,
    _flatten_draft_offer,
    build_product_row,
    build_format_fields,
    draft_product_key,
    upsert_product,
    build_offer_row,
    insert_and_fetch_offer,
)

router = APIRouter()

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_PRODUCT_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_PRODUCT_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
OFFER_KIND_SOURCE_MASTER = "source_master"
OFFER_KIND_PUBLISHED_TARGET = "published_target"


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
    is_reviewed: bool | None = None
    detach_product: bool | None = None


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


class FlyerValidityUpdate(BaseModel):
    valid_from: str | None = None
    valid_to: str | None = None


class FlyerTargetsUpdate(BaseModel):
    supermarket_ids: list[str] = Field(default_factory=list, min_length=1)


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


def _offer_kind(offer: dict) -> str:
    return offer.get("offer_kind") or OFFER_KIND_SOURCE_MASTER


def _flyer_targets(sb, flyer_id: str) -> list[dict]:
    result = (
        sb.table("flyer_targets")
        .select("id, supermarket_id, supermarkets(id, name, address, city, province, postal_code, logo_url)")
        .eq("flyer_id", flyer_id)
        .execute()
    )
    targets: list[dict] = []
    for row in result.data or []:
        supermarket = row.get("supermarkets") or {}
        targets.append(
            {
                "id": row.get("id"),
                "supermarket_id": row["supermarket_id"],
                "supermarket_name": supermarket.get("name"),
                "address": supermarket.get("address"),
                "city": supermarket.get("city"),
                "province": supermarket.get("province"),
                "postal_code": supermarket.get("postal_code"),
                "logo_url": supermarket.get("logo_url"),
            }
        )
    return targets


def _enrich_flyer(sb, flyer: dict) -> dict:
    enriched = dict(flyer)
    enriched["flyer_kind"] = flyer.get("flyer_kind") or "source"
    enriched["targets"] = _flyer_targets(sb, flyer["id"]) if enriched["flyer_kind"] == "source" else []
    return enriched


def _source_flyer_required(flyer: dict) -> None:
    if (flyer.get("flyer_kind") or "source") != "source":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This action is only available on source flyers",
        )


def _published_target_flyers(sb, source_flyer_id: str) -> dict[str, dict]:
    result = (
        sb.table("flyers")
        .select("id, supermarket_id, supermarket_name")
        .eq("source_flyer_id", source_flyer_id)
        .eq("flyer_kind", "published_target")
        .execute()
    )
    return {
        row["supermarket_id"]: {
            "flyer_id": row["id"],
            "supermarket_name": row.get("supermarket_name") or "Supermercato",
        }
        for row in (result.data or [])
        if row.get("id") and row.get("supermarket_id")
    }


def _clone_offer_fields(
    source_offer: dict,
    *,
    flyer_id: str,
    supermarket_id: str,
    supermarket_name: str,
) -> dict:
    return {
        "product_id": source_offer.get("product_id"),
        "draft_name": source_offer.get("draft_name"),
        "draft_brand": source_offer.get("draft_brand"),
        "draft_category": source_offer.get("draft_category"),
        "draft_subcategory": source_offer.get("draft_subcategory"),
        "draft_product_key": source_offer.get("draft_product_key"),
        "draft_image_url": source_offer.get("draft_image_url"),
        "flyer_id": flyer_id,
        "supermarket_id": supermarket_id,
        "supermarket_name": supermarket_name,
        "price_original": source_offer.get("price_original"),
        "price_offer": source_offer.get("price_offer"),
        "discount_pct": source_offer.get("discount_pct"),
        "unit_price": source_offer.get("unit_price"),
        "unit_price_value": source_offer.get("unit_price_value"),
        "unit_price_unit": source_offer.get("unit_price_unit"),
        "offer_type": source_offer.get("offer_type"),
        "offer_notes": source_offer.get("offer_notes"),
        "valid_from": source_offer.get("valid_from"),
        "valid_to": source_offer.get("valid_to"),
        "is_active": source_offer.get("is_active"),
        "raw_text": source_offer.get("raw_text"),
        "confidence_score": source_offer.get("confidence_score"),
        "format": source_offer.get("format"),
        "format_key": source_offer.get("format_key"),
        "format_label": source_offer.get("format_label"),
        "is_confirmed": True,
        "is_reviewed": source_offer.get("is_reviewed", False),
        "offer_kind": OFFER_KIND_PUBLISHED_TARGET,
        "source_offer_id": source_offer["id"],
    }


def _sync_published_clones_for_source_offer(
    sb,
    *,
    source_offer: dict,
    target_flyers: dict[str, dict],
) -> None:
    if not target_flyers:
        return

    clone_rows = (
        sb.table("offers")
        .select("id, supermarket_id, source_offer_id")
        .eq("source_offer_id", source_offer["id"])
        .execute()
    ).data or []
    clones_by_supermarket = {
        row["supermarket_id"]: row
        for row in clone_rows
        if row.get("supermarket_id") and row.get("id")
    }

    for supermarket_id, target in target_flyers.items():
        payload = _clone_offer_fields(
            source_offer,
            flyer_id=target["flyer_id"],
            supermarket_id=supermarket_id,
            supermarket_name=target["supermarket_name"],
        )
        existing = clones_by_supermarket.get(supermarket_id)
        if existing:
            sb.table("offers").update(payload).eq("id", existing["id"]).execute()
            continue
        sb.table("offers").insert({"id": str(uuid.uuid4()), **payload}).execute()

    stale_clone_ids = [
        row["id"]
        for supermarket_id, row in clones_by_supermarket.items()
        if supermarket_id not in target_flyers
    ]
    if stale_clone_ids:
        sb.table("offers").delete().in_("id", stale_clone_ids).execute()


def _profile_supermarket_ids(profile: dict) -> list[str]:
    return managed_supermarket_ids(profile)


def _manager_target_ids(sb, flyer_id: str) -> set[str]:
    return {target["supermarket_id"] for target in _flyer_targets(sb, flyer_id)}


def _supermarket_name_map(sb, supermarket_ids: list[str]) -> dict[str, str]:
    if not supermarket_ids:
        return {}
    result = sb.table("supermarkets").select("id, name").in_("id", supermarket_ids).execute()
    return {
        row["id"]: row.get("name") or "Supermercato"
        for row in (result.data or [])
        if row.get("id")
    }


def _resolve_upload_supermarket_ids(profile: dict, supermarket_ids: list[str]) -> list[str]:
    requested = [value for value in supermarket_ids if value]
    if profile.get("role") != "supermarket_manager":
        return requested

    allowed = _profile_supermarket_ids(profile)
    if not requested:
        if len(allowed) == 1:
            return allowed
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select at least one managed supermarket",
        )
    forbidden = [value for value in requested if value not in allowed]
    if forbidden:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Managers can only upload flyers for their assigned supermarkets",
        )
    return requested


def _duplicate_target_conflicts(
    sb,
    *,
    file_hash: str,
    supermarket_ids: list[str],
    exclude_source_flyer_id: str | None = None,
) -> set[str]:
    if not supermarket_ids:
        return set()

    flyers_resp = sb.table("flyers").select("id, flyer_kind, supermarket_id").eq("file_hash", file_hash).execute()
    rows = flyers_resp.data or []
    conflicts = {
        row["supermarket_id"]
        for row in rows
        if row.get("flyer_kind") == "published_target"
        and row.get("supermarket_id") in supermarket_ids
    }

    source_ids = [
        row["id"]
        for row in rows
        if row.get("flyer_kind") == "source"
        and row.get("id") != exclude_source_flyer_id
    ]
    if source_ids:
        targets_resp = (
            sb.table("flyer_targets")
            .select("supermarket_id")
            .in_("flyer_id", source_ids)
            .in_("supermarket_id", supermarket_ids)
            .execute()
        )
        conflicts.update(
            row["supermarket_id"]
            for row in (targets_resp.data or [])
            if row.get("supermarket_id")
        )
    return conflicts


def _replace_flyer_targets(
    sb,
    *,
    flyer_id: str,
    supermarket_ids: list[str],
) -> None:
    sb.table("flyer_targets").delete().eq("flyer_id", flyer_id).execute()
    if supermarket_ids:
        sb.table("flyer_targets").insert(
            [
                {"flyer_id": flyer_id, "supermarket_id": supermarket_id}
                for supermarket_id in supermarket_ids
            ]
        ).execute()


def _sync_flyer_validity(
    sb,
    *,
    source_flyer_id: str,
    valid_from: str | None,
    valid_to: str | None,
) -> None:
    flyer_update = {"valid_from": valid_from, "valid_to": valid_to}
    sb.table("flyers").update(flyer_update).eq("id", source_flyer_id).execute()
    sb.table("offers").update(flyer_update).eq("flyer_id", source_flyer_id).execute()

    published_targets_resp = (
        sb.table("flyers")
        .select("id")
        .eq("source_flyer_id", source_flyer_id)
        .eq("flyer_kind", "published_target")
        .execute()
    )
    for row in published_targets_resp.data or []:
        published_flyer_id = row.get("id")
        if not published_flyer_id:
            continue
        sb.table("flyers").update(flyer_update).eq("id", published_flyer_id).execute()
        sb.table("offers").update(flyer_update).eq("flyer_id", published_flyer_id).execute()


def _upload_product_image_to_storage(
    sb,
    *,
    storage_prefix: str,
    file_content: bytes,
    content_type: str,
    filename: str | None,
) -> str:
    ext = (filename or "image").rsplit(".", 1)[-1].lower()
    storage_path = f"{storage_prefix}/{uuid.uuid4()}.{ext}"
    sb.storage.from_("product-images").upload(
        path=storage_path,
        file=file_content,
        file_options={"content-type": content_type, "upsert": "true"},
    )
    return sb.storage.from_("product-images").get_public_url(storage_path)


def _normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).strip().lower()
    return normalized or None


def _manager_can_access_flyer(sb, profile: dict, flyer: dict) -> bool:
    if profile.get("role") != "supermarket_manager":
        return True

    managed_ids = set(_profile_supermarket_ids(profile))
    if flyer.get("supermarket_id") in managed_ids:
        return True

    if (flyer.get("flyer_kind") or "source") == "source":
        target_ids = _manager_target_ids(sb, flyer["id"])
        return bool(target_ids.intersection(managed_ids))

    return False


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
    query = (
        sb.table("flyers")
        .select("*")
        .eq("flyer_kind", "source")
        .order("created_at", desc=True)
    )
    response = query.execute()
    flyers = [_enrich_flyer(sb, flyer) for flyer in (response.data or [])]
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
        .eq("flyer_kind", "published_target")
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
            .select("*")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        if not profile_result or not profile_result.data:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        profile = profile_result.data
        profile["managed_supermarket_ids"] = _profile_supermarket_ids(profile) or [
            row["supermarket_id"]
            for row in (
                sb.table("manager_supermarkets")
                .select("supermarket_id")
                .eq("user_id", user_id)
                .execute()
                .data
                or []
            )
            if row.get("supermarket_id")
        ]
        if profile.get("role") not in {"admin", "supermarket_manager"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        _assert_flyer_access(sb, profile, flyer)

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
    supermarket_ids: Annotated[list[str] | None, Form()] = None,
    supermarket_name: str | None = Form(None),
    supermarket_id: str | None = Form(None),
    valid_from: str | None = Form(None),
    valid_to: str | None = Form(None),
    user_id: str = Depends(get_current_user_id),
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Upload a flyer source and attach one or more target supermarkets."""
    requested_ids = list(supermarket_ids or [])
    if supermarket_id:
        requested_ids.append(supermarket_id)
    requested_ids = list(dict.fromkeys(requested_ids))
    requested_ids = _resolve_upload_supermarket_ids(profile, requested_ids)
    if not requested_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select at least one supermarket",
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
    sb = get_supabase()
    conflicts = _duplicate_target_conflicts(
        sb,
        file_hash=file_hash,
        supermarket_ids=requested_ids,
    )
    accepted_ids = [value for value in requested_ids if value not in conflicts]
    if not accepted_ids:
        supermarket_names = _supermarket_name_map(sb, requested_ids)
        blocked_names = [
            supermarket_names.get(supermarket_id, supermarket_id)
            for supermarket_id in requested_ids
        ]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Flyer already exists for: {', '.join(blocked_names)}",
        )
    supermarket_names = _supermarket_name_map(sb, accepted_ids)
    first_target_id = accepted_ids[0]
    supermarket_name = supermarket_names.get(first_target_id, supermarket_name)
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
                "supermarket_id": first_target_id,
                "file_url": file_url,
                "file_type": file_type,
                "file_name": file.filename,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "status": "pending",
                "is_public": False,
                "file_hash": file_hash,
                "flyer_kind": "source",
            }
        )
        .execute()
    )
    source_flyer = row.data[0]
    sb.table("flyer_targets").insert(
        [
            {"flyer_id": source_flyer["id"], "supermarket_id": supermarket_target_id}
            for supermarket_target_id in accepted_ids
        ]
    ).execute()
    enriched = _enrich_flyer(sb, source_flyer)
    enriched["rejected_targets"] = [
        {
            "supermarket_id": supermarket_target_id,
            "supermarket_name": _supermarket_name_map(sb, [supermarket_target_id]).get(
                supermarket_target_id,
                supermarket_target_id,
            ),
        }
        for supermarket_target_id in requested_ids
        if supermarket_target_id in conflicts
    ]
    return enriched


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
    return _enrich_flyer(sb, flyer)


@router.patch("/{flyer_id}")
async def update_flyer_validity(
    flyer_id: str,
    payload: FlyerValidityUpdate,
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    sb = get_supabase()
    result = sb.table("flyers").select("*").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    flyer = result.data
    _source_flyer_required(flyer)
    _assert_flyer_access(sb, profile, flyer)

    _sync_flyer_validity(
        sb,
        source_flyer_id=flyer_id,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
    )
    updated = sb.table("flyers").select("*").eq("id", flyer_id).single().execute().data
    return _enrich_flyer(sb, updated)


@router.get("/{flyer_id}/targets")
async def get_flyer_targets(
    flyer_id: str,
    profile: dict = Depends(require_admin_or_manager),
) -> list[dict]:
    sb = get_supabase()
    result = sb.table("flyers").select("*").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")
    flyer = result.data
    _source_flyer_required(flyer)
    _assert_flyer_access(sb, profile, flyer)
    return _flyer_targets(sb, flyer_id)


@router.put("/{flyer_id}/targets")
@router.patch("/{flyer_id}/targets")
async def update_flyer_targets(
    flyer_id: str,
    payload: FlyerTargetsUpdate,
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    sb = get_supabase()
    result = sb.table("flyers").select("*").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")
    flyer = result.data
    _source_flyer_required(flyer)
    _assert_flyer_access(sb, profile, flyer)
    if flyer.get("is_public"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot modify targets after publication",
        )

    requested_ids = _resolve_upload_supermarket_ids(profile, payload.supermarket_ids)
    conflicts = _duplicate_target_conflicts(
        sb,
        file_hash=flyer.get("file_hash"),
        supermarket_ids=requested_ids,
        exclude_source_flyer_id=flyer_id,
    )
    accepted_ids = [value for value in requested_ids if value not in conflicts]
    if not accepted_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="All selected supermarkets already have this flyer",
        )

    _replace_flyer_targets(sb, flyer_id=flyer_id, supermarket_ids=accepted_ids)
    name_map = _supermarket_name_map(sb, accepted_ids)
    first_target_id = accepted_ids[0]
    sb.table("flyers").update(
        {
            "supermarket_id": first_target_id,
            "supermarket_name": name_map.get(first_target_id),
        }
    ).eq("id", flyer_id).execute()

    updated = sb.table("flyers").select("*").eq("id", flyer_id).single().execute().data
    enriched = _enrich_flyer(sb, updated)
    rejected_names = _supermarket_name_map(sb, list(conflicts))
    enriched["rejected_targets"] = [
        {
            "supermarket_id": supermarket_id,
            "supermarket_name": rejected_names.get(supermarket_id, supermarket_id),
        }
        for supermarket_id in requested_ids
        if supermarket_id in conflicts
    ]
    return enriched


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
    _source_flyer_required(flyer)
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
    _source_flyer_required(flyer)
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
    result = sb.table("flyers").select("id, supermarket_id, supermarket_name, flyer_kind").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _source_flyer_required(result.data)
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
    result = sb.table("flyers").select("id, supermarket_id, supermarket_name, flyer_kind").eq("id", flyer_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _source_flyer_required(result.data)
    _assert_flyer_access(sb, profile, result.data)

    offers_resp = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_SELECT)
        .eq("flyer_id", flyer_id)
        .eq("is_confirmed", True)
        .eq("offer_kind", OFFER_KIND_SOURCE_MASTER)
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
        .select("id, supermarket_id, supermarket_name, valid_from, valid_to, flyer_kind")
        .eq("id", flyer_id)
        .maybe_single()
        .execute()
    )
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")
    flyer = flyer_result.data
    _source_flyer_required(flyer)
    _assert_flyer_access(sb, profile, flyer)

    normalized_unit = normalize_unit_price_measure(payload.unit_price_unit) if payload.unit_price_unit else None
    offer_row = build_offer_row(
        payload, None, flyer["supermarket_id"], flyer.get("supermarket_name"),
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
    flyer_result = sb.table("flyers").select("id, supermarket_id, supermarket_name, flyer_kind").eq("id", flyer_id).maybe_single().execute()
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _source_flyer_required(flyer_result.data)
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
    if payload.detach_product and offer.get("is_confirmed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirmed offers must stay linked to a product",
        )

    offer_fields = {
        k: (normalize_unit_price_measure(v) if k == "unit_price_unit" else v)
        for k, v in {
            "price_offer": payload.price_offer,
            "price_original": payload.price_original,
            "unit_price_value": payload.unit_price_value,
            "unit_price_unit": payload.unit_price_unit,
            "offer_notes": payload.offer_notes,
            "is_reviewed": payload.is_reviewed,
            "product_id": None if payload.detach_product else None,
        }.items()
        if k in sent or (k == "product_id" and payload.detach_product)
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

    product_payload = {
        "name": payload.name,
        "brand": payload.brand,
        "category": payload.category,
        "subcategory": payload.subcategory,
    }
    product_fields = {k: v for k, v in product_payload.items() if k in sent}
    if product_fields:
        draft_fields = {
            f"draft_{k}": v
            for k, v in product_fields.items()
        }
        draft_fields["draft_product_key"] = draft_product_key(
            payload.name if "name" in sent else None,
            payload.brand if "brand" in sent else None,
        )
        if "name" not in sent or "brand" not in sent:
            current = (
                sb.table("offers")
                .select("draft_name, draft_brand")
                .eq("id", offer_id)
                .single()
                .execute()
            )
            current_data = current.data or {}
            draft_fields["draft_product_key"] = draft_product_key(
                payload.name if "name" in sent else current_data.get("draft_name"),
                payload.brand if "brand" in sent else current_data.get("draft_brand"),
            )
        if offer.get("is_confirmed"):
            sb.table("products").update(product_fields).eq("id", offer["product_id"]).execute()
        sb.table("offers").update(draft_fields).eq("id", offer_id).execute()

    updated = (
        sb.table("offers")
        .select(_OFFER_PRODUCT_SELECT)
        .eq("id", offer_id)
        .single()
        .execute()
    )
    updated_offer = updated.data
    if updated_offer.get("is_confirmed") and _offer_kind(updated_offer) == OFFER_KIND_SOURCE_MASTER:
        _sync_published_clones_for_source_offer(
            sb,
            source_offer=updated_offer,
            target_flyers=_published_target_flyers(sb, flyer_id),
        )
        updated = (
            sb.table("offers")
            .select(_OFFER_PRODUCT_SELECT)
            .eq("id", offer_id)
            .single()
            .execute()
        )
    return _flatten_draft_offer(updated.data)


@router.post("/{flyer_id}/draft-offers/{offer_id}/image")
async def upload_draft_offer_image(
    flyer_id: str,
    offer_id: str,
    file: Annotated[UploadFile, File()],
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Upload a staged product image for a draft offer that will create a new product."""
    sb = get_supabase()
    flyer_result = (
        sb.table("flyers")
        .select("id, supermarket_id, supermarket_name, flyer_kind")
        .eq("id", flyer_id)
        .maybe_single()
        .execute()
    )
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _source_flyer_required(flyer_result.data)
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
    if offer.get("is_confirmed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirmed offers must be updated from the catalog product page",
        )
    if offer.get("product_id"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detach the catalog product before uploading a draft image",
        )
    if not file.content_type or file.content_type not in ALLOWED_PRODUCT_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Formato immagine non supportato. Usa JPEG, PNG, WebP o GIF.",
        )

    content = await file.read()
    if len(content) > MAX_PRODUCT_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Immagine troppo grande. Max 10 MB.",
        )

    public_url = _upload_product_image_to_storage(
        sb,
        storage_prefix=f"draft-offers/{offer_id}",
        file_content=content,
        content_type=file.content_type,
        filename=file.filename,
    )
    sb.table("offers").update({"draft_image_url": public_url}).eq("id", offer_id).execute()

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
    """Delete a source offer and any published clones derived from it."""
    sb = get_supabase()
    flyer_result = sb.table("flyers").select("id, supermarket_id, supermarket_name, flyer_kind").eq("id", flyer_id).maybe_single().execute()
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    _source_flyer_required(flyer_result.data)
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

    offer = offer_result.data
    if offer.get("is_confirmed"):
        sb.table("offers").delete().eq("source_offer_id", offer_id).execute()
    sb.table("offers").delete().eq("id", offer_id).execute()


@router.post("/{flyer_id}/offers/confirm")
async def confirm_offers(
    flyer_id: str,
    profile: dict = Depends(require_admin_or_manager),
) -> dict:
    """Confirm source flyer offers and publish one derived flyer per target."""
    sb = get_supabase()
    flyer_result = sb.table("flyers").select("*").eq("id", flyer_id).maybe_single().execute()
    if not flyer_result or not flyer_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flyer not found")

    flyer = flyer_result.data
    _source_flyer_required(flyer)
    _assert_flyer_access(sb, profile, flyer)

    if flyer.get("status") != "done":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot confirm offers: flyer status is '{flyer.get('status')}' (must be 'done')",
        )

    targets = _flyer_targets(sb, flyer_id)
    if not targets:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Add at least one supermarket target before confirmation",
        )

    drafts = (
        sb.table("offers")
        .select("*")
        .eq("flyer_id", flyer_id)
        .eq("is_confirmed", False)
        .execute()
    )
    draft_rows = drafts.data or []
    for draft in draft_rows:
        if draft.get("product_id"):
            continue
        product_id = upsert_product(sb, {
            "name": draft.get("draft_name"),
            "brand": draft.get("draft_brand"),
            "category": draft.get("draft_category"),
            "subcategory": draft.get("draft_subcategory"),
            "image_url": draft.get("draft_image_url"),
        })
        sb.table("offers").update({"product_id": product_id}).eq("id", draft["id"]).execute()

    if draft_rows:
        sb.table("offers").update(
            {"is_confirmed": True, "offer_kind": OFFER_KIND_SOURCE_MASTER}
        ).eq("flyer_id", flyer_id).eq("is_confirmed", False).execute()
    confirmed_count = len(draft_rows)

    source_confirmed = (
        sb.table("offers")
        .select("*", count="exact")
        .eq("flyer_id", flyer_id)
        .eq("is_confirmed", True)
        .eq("offer_kind", OFFER_KIND_SOURCE_MASTER)
        .execute()
    )
    source_offers = source_confirmed.data or []
    source_offer_count = (
        source_confirmed.count
        if isinstance(source_confirmed.count, int)
        else len(source_offers)
    )
    if source_offer_count <= 0:
        return {
            "confirmed": confirmed_count,
            "flyer_id": flyer_id,
            "published_flyers": [],
        }

    existing_target_flyer_by_supermarket = {
        supermarket_id: target["flyer_id"]
        for supermarket_id, target in _published_target_flyers(sb, flyer_id).items()
    }

    published_flyers: list[dict] = []
    for target in targets:
        target_supermarket_id = target["supermarket_id"]
        target_supermarket_name = target.get("supermarket_name") or "Supermercato"
        published_flyer_id = existing_target_flyer_by_supermarket.get(target_supermarket_id)

        if not published_flyer_id:
            inserted = (
                sb.table("flyers")
                .insert(
                    {
                        "user_id": flyer.get("user_id"),
                        "supermarket_id": target_supermarket_id,
                        "supermarket_name": target_supermarket_name,
                        "file_url": flyer.get("file_url"),
                        "file_type": flyer.get("file_type"),
                        "file_name": flyer.get("file_name"),
                        "valid_from": flyer.get("valid_from"),
                        "valid_to": flyer.get("valid_to"),
                        "status": "done",
                        "error_message": None,
                        "products_count": source_offer_count,
                        "pages_count": flyer.get("pages_count"),
                        "extraction_metadata": flyer.get("extraction_metadata"),
                        "is_public": True,
                        "file_hash": flyer.get("file_hash"),
                        "flyer_kind": "published_target",
                        "source_flyer_id": flyer_id,
                    }
                )
                .execute()
            )
            published_flyer_id = inserted.data[0]["id"]
            if draft_rows and not flyer.get("is_public"):
                notify_public_flyer_published(
                    sb,
                    flyer_id=published_flyer_id,
                    supermarket_id=target_supermarket_id,
                    supermarket_name=target_supermarket_name,
                    products_count=source_offer_count,
                )
        else:
            sb.table("flyers").update(
                {
                    "supermarket_name": target_supermarket_name,
                    "status": "done",
                    "products_count": source_offer_count,
                    "valid_from": flyer.get("valid_from"),
                    "valid_to": flyer.get("valid_to"),
                    "is_public": True,
                }
            ).eq("id", published_flyer_id).execute()

        published_flyers.append(
            {
                "flyer_id": published_flyer_id,
                "supermarket_id": target_supermarket_id,
                "supermarket_name": target_supermarket_name,
            }
        )

    target_flyers = _published_target_flyers(sb, flyer_id)
    for source_offer in source_offers:
        _sync_published_clones_for_source_offer(
            sb,
            source_offer=source_offer,
            target_flyers=target_flyers,
        )

    return {
        "confirmed": confirmed_count,
        "flyer_id": flyer_id,
        "published_flyers": published_flyers,
    }


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
