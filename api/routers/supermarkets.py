from fastapi import APIRouter, Query

from core.database import get_supabase

router = APIRouter()


def _nearby_supermarkets(sb, lat: float, lng: float, max_distance_km: float) -> list[dict]:
    response = sb.rpc(
        "nearby_supermarkets",
        {
            "user_lat": lat,
            "user_lng": lng,
            "radius_m": max_distance_km * 1000,
        },
    ).execute()
    return response.data or []


def _merge_distances(rows: list[dict], nearby_rows: list[dict]) -> list[dict]:
    rows_by_id = {row["id"]: row for row in rows}
    distances = {row["id"]: row["distance_km"] for row in nearby_rows}
    return [
        {**rows_by_id[row["id"]], "distance_km": distances[row["id"]]}
        for row in nearby_rows
        if row["id"] in rows_by_id
    ]


@router.get("")
async def list_supermarkets(
    has_active_offers: bool = Query(False),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    max_distance_km: float = Query(10.0, gt=0, le=100),
) -> list[dict]:
    """Return active supermarkets. Public endpoint — no auth required.

    has_active_offers=true: only supermarkets with ≥1 active confirmed offer.
    """
    sb = get_supabase()
    if lat is not None and lng is not None:
        nearby = _nearby_supermarkets(sb, lat, lng, max_distance_km)
        ids = [row["id"] for row in nearby]
        if not ids:
            return []
        resp = sb.table("supermarkets").select("*").in_("id", ids).execute()
        return _merge_distances(resp.data or [], nearby)

    if has_active_offers:
        resp = (
            sb.table("supermarkets")
            .select("*, offers!inner(id)")
            .eq("is_active", True)
            .eq("offers.is_active", True)
            .eq("offers.is_confirmed", True)
            .order("name")
            .execute()
        )
        return [{k: v for k, v in row.items() if k != "offers"} for row in resp.data]
    resp = sb.table("supermarkets").select("*").eq("is_active", True).order("name").execute()
    return resp.data
