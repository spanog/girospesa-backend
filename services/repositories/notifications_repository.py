from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from core.database import get_postgres_cursor, get_supabase, has_direct_postgres


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {key: (str(value) if isinstance(value, UUID) else value) for key, value in row.items()}


def list_notifications(user_id: str) -> list[dict]:
    if not has_direct_postgres():
        return (
            get_supabase()
            .table("app_notifications")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
            .data
        )
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, user_id, kind, title, body, data, read_at, created_at
            FROM public.app_notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
    return [_normalize_row(dict(row)) for row in rows]


def mark_notification_read(notification_id: str, user_id: str) -> dict | None:
    if not has_direct_postgres():
        rows = (
            get_supabase()
            .table("app_notifications")
            .update({"read_at": _now_utc()})
            .eq("id", notification_id)
            .eq("user_id", user_id)
            .execute()
            .data
        )
        return rows[0] if rows else None
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            UPDATE public.app_notifications
            SET read_at = %s
            WHERE id = %s
              AND user_id = %s
            RETURNING id, user_id, kind, title, body, data, read_at, created_at
            """,
            (_now_utc(), notification_id, user_id),
        )
        row = cursor.fetchone()
    return _normalize_row(dict(row)) if row else None


def mark_all_notifications_read(user_id: str) -> None:
    if not has_direct_postgres():
        (
            get_supabase()
            .table("app_notifications")
            .update({"read_at": _now_utc()})
            .eq("user_id", user_id)
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
              AND read_at IS NULL
            """,
            (_now_utc(), user_id),
        )
