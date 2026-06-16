from __future__ import annotations

import json
import select
import time
from datetime import datetime, timezone
from typing import Literal, TypedDict

import psycopg2

from core.database import get_database_dsn, get_postgres_cursor, has_direct_postgres

LIST_SYNC_CHANNEL = "list_sync_events"
LIST_SYNC_HEARTBEAT_SECONDS = 25

ListSyncEventName = Literal["list_updated", "members_updated", "invites_updated"]


class ListSyncEvent(TypedDict):
    list_id: str
    event: ListSyncEventName
    reason: str
    changed_at: str
    id: str


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_list_sync_event(
    list_id: str,
    event: ListSyncEventName,
    reason: str,
    *,
    changed_at: str | None = None,
) -> ListSyncEvent:
    timestamp = changed_at or now_utc_iso()
    return {
        "list_id": list_id,
        "event": event,
        "reason": reason,
        "changed_at": timestamp,
        "id": str(time.time_ns()),
    }


def publish_list_sync_event(
    list_id: str,
    event: ListSyncEventName,
    reason: str,
) -> None:
    if not has_direct_postgres():
        return
    payload = json.dumps(build_list_sync_event(list_id, event, reason))
    with get_postgres_cursor() as cursor:
        cursor.execute("SELECT pg_notify(%s, %s)", (LIST_SYNC_CHANNEL, payload))


def parse_list_sync_event(payload: str) -> ListSyncEvent | None:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    list_id = decoded.get("list_id")
    event = decoded.get("event")
    reason = decoded.get("reason")
    changed_at = decoded.get("changed_at")
    event_id = decoded.get("id")
    if not isinstance(list_id, str):
        return None
    if event not in {"list_updated", "members_updated", "invites_updated"}:
        return None
    if not isinstance(reason, str) or not isinstance(changed_at, str):
        return None
    if not isinstance(event_id, str):
        return None
    return {
        "list_id": list_id,
        "event": event,
        "reason": reason,
        "changed_at": changed_at,
        "id": event_id,
    }


def connect_listener():
    dsn = get_database_dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL or DB_DSN is required for list sync")
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(f"LISTEN {LIST_SYNC_CHANNEL}")
    return connection


def wait_for_list_sync_event(
    connection,
    *,
    timeout_seconds: int = LIST_SYNC_HEARTBEAT_SECONDS,
) -> ListSyncEvent | None:
    if select.select([connection], [], [], timeout_seconds) == ([], [], []):
        return None
    connection.poll()
    while connection.notifies:
        notify = connection.notifies.pop(0)
        event = parse_list_sync_event(notify.payload)
        if event is not None:
            return event
    return None
