"""Database helpers for Supabase/Postgres infrastructure."""

from __future__ import annotations

from functools import lru_cache
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
from supabase import create_client, Client
from core.config import settings


_POSTGRES_POOL_MIN_CONN = 1
_POSTGRES_POOL_MAX_CONN = 5


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def get_database_dsn() -> str:
    return getattr(settings, "db_dsn", "") or getattr(settings, "database_url", "")


def has_direct_postgres() -> bool:
    return bool(get_database_dsn())


@lru_cache(maxsize=1)
def _get_postgres_pool() -> psycopg2.pool.SimpleConnectionPool:
    dsn = get_database_dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL or DB_DSN is required for direct Postgres access")
    return psycopg2.pool.SimpleConnectionPool(
        _POSTGRES_POOL_MIN_CONN,
        _POSTGRES_POOL_MAX_CONN,
        dsn=dsn,
    )


@contextmanager
def get_postgres_cursor():
    connection = _get_postgres_pool().getconn()
    try:
        connection.autocommit = True
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            yield cursor
    finally:
        _get_postgres_pool().putconn(connection)
