from __future__ import annotations


def pick_search_coordinate(
    profile: dict, search_key: str, home_key: str
) -> float | None:
    val = profile.get(search_key)
    return val if val is not None else profile.get(home_key)


def nearby_distances(
    sb, lat: float, lng: float, max_distance_km: float
) -> dict[str, float]:
    response = sb.rpc(
        "nearby_supermarkets",
        {"user_lat": lat, "user_lng": lng, "radius_m": max_distance_km * 1000},
    ).execute()
    return {row["id"]: row["distance_km"] for row in (response.data or [])}


def load_nearby_distances(sb, user_id: str) -> dict[str, float] | None:
    """Return {supermarket_id: distance_km} for user's search radius, or None."""
    profile_resp = (
        sb.table("user_profiles")
        .select("home_lat, home_lng, search_lat, search_lng, max_distance_km")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    profile: dict = (profile_resp.data if profile_resp is not None else None) or {}
    lat = pick_search_coordinate(profile, "search_lat", "home_lat")
    lng = pick_search_coordinate(profile, "search_lng", "home_lng")
    max_km = profile.get("max_distance_km") or 10
    if lat is None or lng is None:
        return None
    return nearby_distances(sb, lat, lng, max_km)
