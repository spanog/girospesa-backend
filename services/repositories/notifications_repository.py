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


def _normalize_ids(ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for notification_id in ids:
        if notification_id in seen:
            continue
        seen.add(notification_id)
        normalized.append(notification_id)
    return normalized


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


def delete_notification(notification_id: str, user_id: str) -> bool:
    if not has_direct_postgres():
        return _delete_single_notification_supabase(notification_id, user_id)
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM public.app_notifications
            WHERE id = %s
              AND user_id = %s
            """,
            (notification_id, user_id),
        )
        return cursor.rowcount > 0


def _delete_single_notification_supabase(notification_id: str, user_id: str) -> bool:
    selected_ids = _select_notification_ids_supabase([notification_id], user_id)
    if not selected_ids:
        return False
    (
        get_supabase()
        .table("app_notifications")
        .delete()
        .eq("id", notification_id)
        .eq("user_id", user_id)
        .execute()
    )
    remaining_ids = _select_notification_ids_supabase(selected_ids, user_id)
    return notification_id in _effective_deleted_ids(selected_ids, remaining_ids)


def delete_notifications(notification_ids: list[str], user_id: str) -> dict[str, list[str]]:
    normalized_ids = _normalize_ids(notification_ids)
    if not normalized_ids:
        return {"deleted_ids": [], "missing_ids": []}
    if not has_direct_postgres():
        return _delete_notifications_supabase(normalized_ids, user_id)
    return _delete_notifications_postgres(normalized_ids, user_id)


def _delete_notifications_supabase(
    notification_ids: list[str],
    user_id: str,
) -> dict[str, list[str]]:
    selected_ids = _select_notification_ids_supabase(notification_ids, user_id)
    if not selected_ids:
        return _build_delete_result(notification_ids, [])
    (
        get_supabase()
        .table("app_notifications")
        .delete()
        .eq("user_id", user_id)
        .in_("id", selected_ids)
        .execute()
    )
    remaining_ids = _select_notification_ids_supabase(selected_ids, user_id)
    effective_deleted_ids = _effective_deleted_ids(selected_ids, remaining_ids)
    return _build_delete_result(notification_ids, effective_deleted_ids)


def _delete_notifications_postgres(
    notification_ids: list[str],
    user_id: str,
) -> dict[str, list[str]]:
    with get_postgres_cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM public.app_notifications
            WHERE user_id = %s
              AND id = ANY(%s::uuid[])
            RETURNING id
            """,
            (user_id, notification_ids),
        )
        rows = cursor.fetchall()
    deleted_ids = [str(row["id"]) for row in rows]
    return _build_delete_result(notification_ids, deleted_ids)


def _build_delete_result(
    requested_ids: list[str],
    deleted_ids: list[str],
) -> dict[str, list[str]]:
    deleted_set = set(deleted_ids)
    ordered_deleted_ids = [
        notification_id for notification_id in requested_ids if notification_id in deleted_set
    ]
    missing_ids = [notification_id for notification_id in requested_ids if notification_id not in deleted_set]
    return {"deleted_ids": ordered_deleted_ids, "missing_ids": missing_ids}


def _deleted_ids_from_rows(rows: list[dict]) -> list[str]:
    return [str(row["id"]) for row in rows if row.get("id") is not None]


def _select_notification_ids_supabase(
    notification_ids: list[str],
    user_id: str,
) -> list[str]:
    rows = (
        get_supabase()
        .table("app_notifications")
        .select("id")
        .eq("user_id", user_id)
        .in_("id", notification_ids)
        .execute()
        .data
    ) or []
    selected_ids = _deleted_ids_from_rows(rows)
    selected_set = set(selected_ids)
    return [notification_id for notification_id in notification_ids if notification_id in selected_set]


def _effective_deleted_ids(
    selected_ids: list[str],
    remaining_ids: list[str],
) -> list[str]:
    remaining_set = set(remaining_ids)
    return [notification_id for notification_id in selected_ids if notification_id not in remaining_set]
