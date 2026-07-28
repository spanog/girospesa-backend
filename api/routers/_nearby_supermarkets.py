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
