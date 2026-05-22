from __future__ import annotations

from datetime import datetime, timezone
import time
from uuid import UUID

import psycopg2.extras
from psycopg2 import errors as psycopg2_errors
from fastapi import HTTPException

from core.database import get_postgres_cursor, get_supabase, has_direct_postgres
from services.admin_seed import find_user_by_email


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_db_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    normalized: dict = {}
    for key, value in row.items():
        normalized[key] = str(value) if isinstance(value, UUID) else value
    return normalized


def _wait_for_auth_user(user_id: str, *, timeout_seconds: float = 3.0) -> None:
    """Auth bootstrap can lag briefly before FK targets become visible."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with get_postgres_cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM auth.users
                WHERE id = %s
                LIMIT 1
                """,
                (user_id,),
            )
            if cursor.fetchone():
                return
            cursor.execute(
                """
                SELECT 1
                FROM public.user_profiles
                WHERE id = %s
                LIMIT 1
                """,
                (user_id,),
            )
            if cursor.fetchone():
                return
        time.sleep(0.1)
    raise HTTPException(status_code=409, detail="User profile is not ready yet")


def _insert_owned_list_row(
    cursor: psycopg2.extensions.cursor,
    *,
    user_id: str,
    name: str,
    items: list[dict] | None,
    is_active: bool,
    is_default: bool,
) -> dict:
    deadline = time.monotonic() + 3.0
    params = (user_id, name, psycopg2.extras.Json(items or []), is_active, is_default)
    while True:
        try:
            cursor.execute(
                """
                INSERT INTO public.shopping_lists (user_id, name, items, is_active, is_default)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                RETURNING id, user_id, name, items, is_active, created_at, updated_at
                """,
                params,
            )
            return cursor.fetchone()
        except psycopg2_errors.ForeignKeyViolation:
            if time.monotonic() >= deadline:
                raise
            _wait_for_auth_user(user_id)
            time.sleep(0.1)


def verify_member(sb: object, list_id: str, user_id: str) -> None:
    if not has_direct_postgres():
        result = (
            sb.table("list_members")
            .select("id")
            .eq("list_id", list_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=403, detail="Not a member of this list")
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM public.list_members
            WHERE list_id = %s
              AND user_id = %s
            LIMIT 1
            """,
            (list_id, user_id),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this list")


def verify_owner(sb: object, list_id: str, user_id: str) -> None:
    if not has_direct_postgres():
        member = (
            sb.table("list_members")
            .select("role")
            .eq("list_id", list_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not member.data or member.data[0]["role"] != "owner":
            raise HTTPException(status_code=403, detail="Only the owner can perform this action")
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT role
            FROM public.list_members
            WHERE list_id = %s
              AND user_id = %s
            LIMIT 1
            """,
            (list_id, user_id),
        )
        row = cursor.fetchone()
    if not row or row["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can perform this action")


def profile_row(sb: object, user_id: str) -> dict:
    if not has_direct_postgres():
        response = (
            sb.table("user_profiles")
            .select("active_list_id, display_name")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return response.data
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT active_list_id, display_name
            FROM public.user_profiles
            WHERE id = %s
            """,
            (user_id,),
        )
        row = cursor.fetchone()
    return _normalize_db_row(dict(row)) if row else {"active_list_id": None, "display_name": None}


def set_active_list_id(user_id: str, list_id: str | None) -> None:
    if not has_direct_postgres():
        sb = get_supabase()
        sb.table("user_profiles").update({"active_list_id": list_id}).eq("id", user_id).execute()
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            UPDATE public.user_profiles
            SET active_list_id = %s
            WHERE id = %s
            """,
            (list_id, user_id),
        )


def default_list_id_for_user(sb: object, user_id: str) -> str | None:
    if not has_direct_postgres():
        response = (
            sb.table("shopping_lists")
            .select("id")
            .eq("user_id", user_id)
            .eq("is_default", True)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        return response.data[0]["id"]
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM public.shopping_lists
            WHERE user_id = %s
              AND is_default = true
            ORDER BY created_at ASC NULLS LAST, id ASC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
    return str(row["id"]) if row else None


def is_default_by_list_id(list_id: str) -> bool:
    if not has_direct_postgres():
        sb = get_supabase()
        response = (
            sb.table("shopping_lists")
            .select("is_default")
            .eq("id", list_id)
            .limit(1)
            .execute()
        )
        return bool(response.data and response.data[0].get("is_default"))
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT is_default
            FROM public.shopping_lists
            WHERE id = %s
            """,
            (list_id,),
        )
        row = cursor.fetchone()
    return bool(row["is_default"]) if row else False


def list_default_flags(list_ids: list[str]) -> dict[str, bool]:
    if not list_ids:
        return {}
    if not has_direct_postgres():
        sb = get_supabase()
        rows = sb.table("shopping_lists").select("id, is_default").in_("id", list_ids).execute().data
        return {row["id"]: bool(row.get("is_default")) for row in rows}
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, is_default
            FROM public.shopping_lists
            WHERE id = ANY(%s::uuid[])
            """,
            (list_ids,),
        )
        rows = cursor.fetchall()
    return {str(row["id"]): bool(row["is_default"]) for row in rows}


def create_owned_list(
    *,
    user_id: str,
    name: str,
    is_default: bool,
    is_active: bool = True,
    items: list[dict] | None = None,
) -> dict:
    if not has_direct_postgres():
        sb = get_supabase()
        row = (
            sb.table("shopping_lists")
            .insert({
                "user_id": user_id,
                "name": name,
                "items": items or [],
                "is_active": is_active,
                "is_default": is_default,
            })
            .execute()
            .data[0]
        )
        (
            sb.table("list_members")
            .insert({"list_id": row["id"], "user_id": user_id, "role": "owner"})
            .execute()
        )
        return row
    with get_postgres_cursor() as cursor:
        try:
            row = _insert_owned_list_row(
                cursor,
                user_id=user_id,
                name=name,
                items=items,
                is_active=is_active,
                is_default=is_default,
            )
        except psycopg2_errors.ForeignKeyViolation as exc:
            raise HTTPException(status_code=409, detail="User profile is not ready yet") from exc
    normalized = _normalize_db_row(dict(row))
    if normalized is None:
        raise HTTPException(status_code=500, detail="Failed to create shopping list")
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.list_members (list_id, user_id, role)
            VALUES (%s, %s, 'owner')
            ON CONFLICT (list_id, user_id) DO NOTHING
            """,
            (normalized["id"], user_id),
        )
    return normalized


def shopping_list_row(list_id: str) -> dict:
    if not has_direct_postgres():
        sb = get_supabase()
        return sb.table("shopping_lists").select("*").eq("id", list_id).single().execute().data
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, user_id, name, items, is_active, created_at, updated_at
            FROM public.shopping_lists
            WHERE id = %s
            LIMIT 1
            """,
            (list_id,),
        )
        row = cursor.fetchone()
    normalized = _normalize_db_row(dict(row)) if row else None
    if normalized is None:
        raise HTTPException(status_code=404, detail="List not found")
    return normalized


def shopping_list_rows(list_ids: list[str]) -> list[dict]:
    if not list_ids:
        return []
    if not has_direct_postgres():
        sb = get_supabase()
        return (
            sb.table("shopping_lists")
            .select("*")
            .in_("id", list_ids)
            .order("updated_at", desc=True)
            .execute()
            .data
        )
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, user_id, name, items, is_active, created_at, updated_at
            FROM public.shopping_lists
            WHERE id = ANY(%s::uuid[])
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
            """,
            (list_ids,),
        )
        rows = cursor.fetchall()
    return [_normalize_db_row(dict(row)) for row in rows]


def rename_shopping_list(list_id: str, name: str) -> None:
    if not has_direct_postgres():
        get_supabase().table("shopping_lists").update({"name": name}).eq("id", list_id).execute()
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            UPDATE public.shopping_lists
            SET name = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (name, list_id),
        )


def delete_shopping_list(list_id: str) -> None:
    if not has_direct_postgres():
        get_supabase().table("shopping_lists").delete().eq("id", list_id).execute()
        return
    with get_postgres_cursor() as cursor:
        cursor.execute("DELETE FROM public.shopping_lists WHERE id = %s", (list_id,))


def visible_memberships(sb: object, user_id: str) -> list[dict]:
    if not has_direct_postgres():
        return sb.table("list_members").select("list_id, role").eq("user_id", user_id).execute().data
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT list_id, role
            FROM public.list_members
            WHERE user_id = %s
            ORDER BY list_id ASC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
    return [_normalize_db_row(dict(row)) for row in rows]


def list_member_role(sb: object, list_id: str, user_id: str) -> str | None:
    if not has_direct_postgres():
        response = (
            sb.table("list_members")
            .select("role")
            .eq("list_id", list_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        return response.data[0]["role"]
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT role
            FROM public.list_members
            WHERE list_id = %s
              AND user_id = %s
            LIMIT 1
            """,
            (list_id, user_id),
        )
        row = cursor.fetchone()
    return row["role"] if row else None


def member_counts(list_ids: list[str]) -> dict[str, int]:
    if not list_ids:
        return {}
    if not has_direct_postgres():
        sb = get_supabase()
        rows = sb.table("list_members").select("list_id, user_id").in_("list_id", list_ids).execute().data
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["list_id"]] = counts.get(row["list_id"], 0) + 1
        return counts
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT list_id, COUNT(*)::int AS member_count
            FROM public.list_members
            WHERE list_id = ANY(%s::uuid[])
            GROUP BY list_id
            """,
            (list_ids,),
        )
        rows = cursor.fetchall()
    return {str(row["list_id"]): int(row["member_count"]) for row in rows}


def pending_list_invites_for_user(user_id: str) -> list[dict]:
    if not has_direct_postgres():
        sb = get_supabase()
        return (
            sb.table("list_invites")
            .select("*")
            .eq("invited_user_id", user_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .execute()
            .data
        )
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM public.list_invites
            WHERE invited_user_id = %s
              AND status = 'pending'
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
    return [_normalize_db_row(dict(row)) for row in rows]


def invite_for_user(invite_id: str, user_id: str) -> dict | None:
    if not has_direct_postgres():
        sb = get_supabase()
        rows = (
            sb.table("list_invites")
            .select("*")
            .eq("id", invite_id)
            .eq("invited_user_id", user_id)
            .eq("status", "pending")
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM public.list_invites
            WHERE id = %s
              AND invited_user_id = %s
              AND status = 'pending'
            LIMIT 1
            """,
            (invite_id, user_id),
        )
        row = cursor.fetchone()
    return _normalize_db_row(dict(row)) if row else None


def existing_member(list_id: str, user_id: str) -> bool:
    if not has_direct_postgres():
        sb = get_supabase()
        rows = (
            sb.table("list_members")
            .select("id")
            .eq("list_id", list_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
        )
        return bool(rows)
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM public.list_members
            WHERE list_id = %s
              AND user_id = %s
            LIMIT 1
            """,
            (list_id, user_id),
        )
        row = cursor.fetchone()
    return row is not None


def insert_member(list_id: str, user_id: str, role: str, invited_by: str | None = None) -> None:
    if not has_direct_postgres():
        get_supabase().table("list_members").upsert(
            {"list_id": list_id, "user_id": user_id, "role": role, "invited_by": invited_by},
            on_conflict="list_id,user_id",
        ).execute()
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.list_members (list_id, user_id, role, invited_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (list_id, user_id) DO NOTHING
            """,
            (list_id, user_id, role, invited_by),
        )


def delete_member(list_id: str, user_id: str) -> None:
    if not has_direct_postgres():
        (
            get_supabase()
            .table("list_members")
            .delete()
            .eq("list_id", list_id)
            .eq("user_id", user_id)
            .execute()
        )
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM public.list_members
            WHERE list_id = %s
              AND user_id = %s
            """,
            (list_id, user_id),
        )


def set_invite_status(invite_id: str, *, status: str, accepted_by: str | None = None) -> None:
    accepted_at = _now_utc() if status == "accepted" else None
    declined_at = _now_utc() if status == "declined" else None
    if not has_direct_postgres():
        payload = {"status": status}
        if accepted_at:
            payload["accepted_at"] = accepted_at
        if accepted_by:
            payload["accepted_by"] = accepted_by
        if declined_at:
            payload["declined_at"] = declined_at
        get_supabase().table("list_invites").update(payload).eq("id", invite_id).execute()
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            UPDATE public.list_invites
            SET status = %s,
                accepted_at = COALESCE(%s, accepted_at),
                accepted_by = COALESCE(%s, accepted_by),
                declined_at = COALESCE(%s, declined_at)
            WHERE id = %s
            """,
            (status, accepted_at, accepted_by, declined_at, invite_id),
        )


def pending_invite_for_target(list_id: str, invited_user_id: str) -> dict | None:
    if not has_direct_postgres():
        sb = get_supabase()
        rows = (
            sb.table("list_invites")
            .select("id")
            .eq("list_id", list_id)
            .eq("invited_user_id", invited_user_id)
            .eq("status", "pending")
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM public.list_invites
            WHERE list_id = %s
              AND invited_user_id = %s
              AND status = 'pending'
            LIMIT 1
            """,
            (list_id, invited_user_id),
        )
        row = cursor.fetchone()
    return _normalize_db_row(dict(row)) if row else None


def insert_list_invite(list_id: str, invited_by: str, invited_user_id: str, email: str) -> dict:
    if not has_direct_postgres():
        sb = get_supabase()
        return (
            sb.table("list_invites")
            .insert({
                "list_id": list_id,
                "invited_by": invited_by,
                "invited_user_id": invited_user_id,
                "email": email,
            })
            .execute()
            .data[0]
        )
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.list_invites (list_id, invited_by, invited_user_id, email)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (list_id, invited_by, invited_user_id, email),
        )
        row = cursor.fetchone()
    normalized = _normalize_db_row(dict(row))
    if normalized is None:
        raise HTTPException(status_code=500, detail="Failed to create invite")
    return normalized


def create_app_notification(user_id: str, *, kind: str, title: str, body: str, data: dict) -> dict:
    if not has_direct_postgres():
        sb = get_supabase()
        return (
            sb.table("app_notifications")
            .insert({
                "user_id": user_id,
                "kind": kind,
                "title": title,
                "body": body,
                "data": data,
            })
            .execute()
            .data[0]
        )
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.app_notifications (user_id, kind, title, body, data)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            RETURNING id, user_id, kind, title, body, data, read_at, created_at
            """,
            (user_id, kind, title, body, psycopg2.extras.Json(data)),
        )
        row = cursor.fetchone()
    normalized = _normalize_db_row(dict(row))
    if normalized is None:
        raise HTTPException(status_code=500, detail="Failed to create notification")
    return normalized


def mark_invite_notifications_read(invite_id: str, user_id: str) -> None:
    if not has_direct_postgres():
        sb = get_supabase()
        (
            sb.table("app_notifications")
            .update({"read_at": _now_utc()})
            .eq("user_id", user_id)
            .eq("kind", "list_invite")
            .contains("data", {"invite_id": invite_id})
            .is_("read_at", None)
            .execute()
        )
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            UPDATE public.app_notifications
            SET read_at = %s
            WHERE user_id = %s
              AND kind = 'list_invite'
              AND read_at IS NULL
              AND data->>'invite_id' = %s
            """,
            (_now_utc(), user_id, invite_id),
        )


def delete_invite_notifications(invite_id: str, user_id: str) -> None:
    if not has_direct_postgres():
        sb = get_supabase()
        (
            sb.table("app_notifications")
            .delete()
            .eq("user_id", user_id)
            .eq("kind", "list_invite")
            .contains("data", {"invite_id": invite_id})
            .execute()
        )
        return
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM public.app_notifications
            WHERE user_id = %s
              AND kind = 'list_invite'
              AND data->>'invite_id' = %s
            """,
            (user_id, invite_id),
        )


def auth_user_by_email(email: str) -> dict | None:
    if not has_direct_postgres():
        user = find_user_by_email(get_supabase().auth.admin, email)
        if user is None:
            return None
        return {"id": user.id, "email": getattr(user, "email", email)}
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, email
            FROM auth.users
            WHERE lower(email) = lower(%s)
            LIMIT 1
            """,
            (email,),
        )
        row = cursor.fetchone()
    return _normalize_db_row(dict(row)) if row else None


def impacted_user_ids_for_list(list_id: str) -> set[str]:
    if not has_direct_postgres():
        sb = get_supabase()
        rows = sb.table("list_members").select("user_id").eq("list_id", list_id).execute().data
        return {row["user_id"] for row in rows}
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id
            FROM public.list_members
            WHERE list_id = %s
            """,
            (list_id,),
        )
        rows = cursor.fetchall()
    return {str(row["user_id"]) for row in rows}


def impacted_member_user_ids_for_list(list_id: str) -> list[str]:
    if not has_direct_postgres():
        sb = get_supabase()
        rows = (
            sb.table("list_members")
            .select("user_id")
            .eq("list_id", list_id)
            .eq("role", "member")
            .execute()
            .data
        )
        return [row["user_id"] for row in rows]
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id
            FROM public.list_members
            WHERE list_id = %s
              AND role = 'member'
            ORDER BY user_id ASC
            """,
            (list_id,),
        )
        rows = cursor.fetchall()
    return [str(row["user_id"]) for row in rows]
