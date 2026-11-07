"""Database helpers for Supabase/Postgres infrastructure."""

from __future__ import annotations

from functools import lru_cache
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
from supabase import Client
from core.config import settings
from core.postgres_pool import PostgresConnectionPool
from core.supabase_client import create_supabase_client as create_client


_POSTGRES_POOL_MIN_CONN = 1
_POSTGRES_POOL_MAX_CONN = 5


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_secret_key)


def get_database_dsn() -> str:
    return getattr(settings, "db_dsn", "") or getattr(settings, "database_url", "")


def has_direct_postgres() -> bool:
    return bool(get_database_dsn())


@lru_cache(maxsize=1)
def _get_postgres_pool() -> PostgresConnectionPool:
    dsn = get_database_dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL or DB_DSN is required for direct Postgres access")
    return PostgresConnectionPool(
        lambda: psycopg2.pool.ThreadedConnectionPool(
            _POSTGRES_POOL_MIN_CONN, _POSTGRES_POOL_MAX_CONN, dsn=dsn,
        ),
        _POSTGRES_POOL_MAX_CONN,
    )


@contextmanager
def get_postgres_cursor():
    with _get_postgres_pool().connection() as connection:
        connection.autocommit = True
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            yield cursor
