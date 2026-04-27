from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from core.auth import get_current_user_id
from core.database import get_supabase

router = APIRouter()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/{token}")
async def get_invite(token: str) -> dict:
    """Public — validate token and return list name + inviter. No auth required."""
    sb = get_supabase()
    invite_resp = (
        sb.table("list_invites")
        .select("*, shopping_lists(name)")
        .eq("token", token)
        .eq("status", "pending")
        .execute()
    )
    if not invite_resp.data:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    invite = invite_resp.data[0]

    if invite["expires_at"] < _now_utc():
        raise HTTPException(status_code=410, detail="Invite has expired")

    # Resolve inviter display_name via a separate query (no direct FK to user_profiles)
    profile_resp = (
        sb.table("user_profiles")
        .select("display_name")
        .eq("id", invite["invited_by"])
        .execute()
    )
    invited_by_name = profile_resp.data[0]["display_name"] if profile_resp.data else None

    return {
        "list_name": invite["shopping_lists"]["name"],
        "invited_by": invited_by_name,
        "expires_at": invite["expires_at"],
    }


@router.post("/{token}/accept")
async def accept_invite(
    token: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict:
    """Auth required — add user as list member. Idempotent."""
    sb = get_supabase()
    invite_resp = (
        sb.table("list_invites")
        .select("*")
        .eq("token", token)
        .eq("status", "pending")
        .execute()
    )
    if not invite_resp.data:
        raise HTTPException(status_code=404, detail="Invite not found or expired")

    invite = invite_resp.data[0]

    if invite["expires_at"] < _now_utc():
        raise HTTPException(status_code=410, detail="Invite has expired")

    list_id = invite["list_id"]

    # Idempotent insert into list_members
    existing = (
        sb.table("list_members")
        .select("id")
        .eq("list_id", list_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not existing.data:
        sb.table("list_members").insert({
            "list_id": list_id,
            "user_id": user_id,
            "role": "member",
            "invited_by": invite["invited_by"],
        }).execute()

    # Mark invite as accepted
    sb.table("list_invites").update({
        "status": "accepted",
        "accepted_at": _now_utc(),
        "accepted_by": user_id,
    }).eq("token", token).execute()

    return {"list_id": list_id}
