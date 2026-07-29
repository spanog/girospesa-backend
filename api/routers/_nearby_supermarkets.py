"""Shared geographic lookup helpers for public discovery endpoints."""
from __future__ import annotations


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
