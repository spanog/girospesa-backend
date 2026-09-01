"""Shared geographic lookup helpers for public discovery endpoints."""
from __future__ import annotations

_CURRENT_PUBLIC_OFFER_SUPERMARKETS_RPC = "current_public_offer_supermarket_ids"


def nearby_supermarket_distances(
    sb, lat: float, lng: float, max_distance_km: float
) -> dict[str, float]:
    """Map every supermarket in range to its distance from the active location."""
    response = sb.rpc(
        "nearby_supermarkets",
        {
            "user_lat": lat,
            "user_lng": lng,
            "radius_m": max_distance_km * 1000,
        },
    ).execute()
    return {
        row["id"]: float(row["distance_km"])
        for row in (response.data or [])
        if row.get("id") is not None and row.get("distance_km") is not None
    }


def active_nearby_supermarkets(
    sb, distances_by_supermarket_id: dict[str, float]
) -> list[dict]:
    """Return nearby branches with at least one public, current offer."""
    ids = list(distances_by_supermarket_id)
    if not ids:
        return []
    offer_ids = _active_offer_supermarket_ids(sb, ids)
    if not offer_ids:
        return []
    rows = sb.table("supermarkets").select("*").in_("id", offer_ids).execute().data or []
    return _with_distances(rows, distances_by_supermarket_id)


def _active_offer_supermarket_ids(sb, supermarket_ids: list[str]) -> list[str]:
    response = sb.rpc(
        _CURRENT_PUBLIC_OFFER_SUPERMARKETS_RPC,
        {"candidate_supermarket_ids": supermarket_ids},
    )
    rows = response.execute().data or []
    return [row["id"] for row in rows if row.get("id")]


def _with_distances(rows: list[dict], distances: dict[str, float]) -> list[dict]:
    enriched = [
        {**row, "distance_km": distances[row["id"]]}
        for row in rows
        if row.get("id") in distances
    ]
    return sorted(enriched, key=lambda row: (row["distance_km"], row["name"]))


def request_location(
    sb, user_id: str | None, guest_location: tuple[float, float, float] | None
) -> tuple[float, float, float] | None:
    """Resolve profile location for members and signed-cookie location for guests."""
    if user_id is None:
        return guest_location
    profile = _location_profile(sb, user_id)
    resolved_lat = profile.get("search_lat")
    resolved_lng = profile.get("search_lng")
    if resolved_lat is None or resolved_lng is None:
        resolved_lat = profile.get("home_lat")
        resolved_lng = profile.get("home_lng")
    if resolved_lat is None or resolved_lng is None:
        return None
    radius = profile.get("max_distance_km") or 10.0
    return float(resolved_lat), float(resolved_lng), float(radius)


def _location_profile(sb, user_id: str) -> dict:
    return (
        sb.table("user_profiles")
        .select("search_lat, search_lng, home_lat, home_lng, max_distance_km")
        .eq("id", user_id)
        .maybe_single()
        .execute()
        .data
        or {}
    )
