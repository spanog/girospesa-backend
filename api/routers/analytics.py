"""
B2B analytics endpoint — aggregated, anonymized purchase intent data for GDO chains.
Authenticated with API key, not user JWT.
"""

from fastapi import APIRouter, Header, HTTPException

from core.config import settings
from core.database import get_supabase

router = APIRouter()

# B2B API key — set via environment variable B2B_API_KEY (add to .env)
_B2B_API_KEY: str = ""
try:
    from core.config import Settings
    _B2B_API_KEY = getattr(settings, "b2b_api_key", "")
except Exception:
    pass


@router.get("/b2b")
async def b2b_analytics(x_api_key: str = Header(..., alias="X-API-Key")) -> dict:
    """Return aggregated, anonymized purchase intent data for GDO chains."""
    if not _B2B_API_KEY or x_api_key != _B2B_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    sb = get_supabase()

    # Top 20 most-added items across all shopping lists (anonymized)
    lists_resp = sb.table("shopping_lists").select("items").execute()
    item_counts: dict[str, int] = {}
    for row in lists_resp.data:
        for item in row.get("items", []):
            name = item.get("name", "").lower().strip()
            if name:
                item_counts[name] = item_counts.get(name, 0) + 1

    top_items = sorted(item_counts.items(), key=lambda x: -x[1])[:20]

    return {
        "top_requested_items": [{"name": name, "count": count} for name, count in top_items],
        "total_lists": len(lists_resp.data),
    }
