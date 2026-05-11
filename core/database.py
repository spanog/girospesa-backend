"""Database helpers for Supabase/Postgres infrastructure."""

from __future__ import annotations

from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from supabase import create_client, Client
from core.config import settings

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _client


def get_database_dsn() -> str:
    return settings.db_dsn or settings.database_url


def has_direct_postgres() -> bool:
    return bool(get_database_dsn())


@contextmanager
def get_postgres_cursor():
    dsn = get_database_dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL or DB_DSN is required for direct Postgres access")
    connection = psycopg2.connect(dsn)
    try:
        connection.autocommit = True
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            yield cursor
    finally:
        connection.close()
