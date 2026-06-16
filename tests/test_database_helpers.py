from __future__ import annotations

import importlib
import os
import sys
import types
from unittest.mock import MagicMock


def _load_database_module(monkeypatch):
    sys.modules.pop("core.database", None)
    monkeypatch.setitem(
        sys.modules,
        "core.config",
        types.SimpleNamespace(
            settings=types.SimpleNamespace(
                supabase_url="http://supabase.local",
                supabase_service_role_key="service-role",
                db_dsn="postgresql://example",
                database_url="",
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "supabase",
        types.SimpleNamespace(create_client=MagicMock(), Client=object),
    )
    return importlib.import_module("core.database")


def test_get_supabase_is_singleton(monkeypatch):
    database = _load_database_module(monkeypatch)
    client = object()
    database.create_client.return_value = client

    first = database.get_supabase()
    second = database.get_supabase()

    assert first is client
    assert second is client
    database.create_client.assert_called_once_with(
        "http://supabase.local",
        "service-role",
    )


def test_get_postgres_cursor_reuses_pool(monkeypatch):
    database = _load_database_module(monkeypatch)
    cursor = MagicMock()
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    cursor_cm.__exit__.return_value = False
    connection = MagicMock()
    connection.cursor.return_value = cursor_cm
    pool = MagicMock()
    pool.getconn.return_value = connection
    pool_factory = MagicMock(return_value=pool)
    monkeypatch.setattr(database.psycopg2.pool, "SimpleConnectionPool", pool_factory)

    with database.get_postgres_cursor() as first_cursor:
        assert first_cursor is cursor
    with database.get_postgres_cursor() as second_cursor:
        assert second_cursor is cursor

    pool_factory.assert_called_once_with(1, 5, dsn="postgresql://example")
    assert pool.getconn.call_count == 2
    assert pool.putconn.call_count == 2
