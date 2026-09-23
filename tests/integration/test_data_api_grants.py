"""Integration coverage for explicit Supabase Data API grants."""

from __future__ import annotations

import os

import psycopg2


_SERVICE_TABLES = (
    "public.offers",
    "public.notification_jobs",
    "public.municipalities",
    "public.list_sync_operations",
)
_DATA_API_ROLES = ("anon", "authenticated", "service_role")
_DML_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _has_all_dml_privileges(cur, role: str, relation: str) -> bool:
    return all(
        _has_table_privilege(cur, role, relation, privilege)
        for privilege in _DML_PRIVILEGES
    )


def _has_table_privilege(cur, role: str, relation: str, privilege: str) -> bool:
    cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, relation, privilege))
    return bool(cur.fetchone()[0])


def _assert_historical_table_grants(cursor) -> None:
    for relation in _SERVICE_TABLES:
        assert _has_all_dml_privileges(cursor, "service_role", relation)

    assert _has_all_dml_privileges(cursor, "anon", "public.offers")
    assert _has_all_dml_privileges(cursor, "authenticated", "public.offers")


def _assert_future_table_has_no_implicit_grants(cursor) -> None:
    cursor.execute("CREATE TABLE public.data_api_grant_probe (id UUID PRIMARY KEY)")
    for role in _DATA_API_ROLES:
        assert not _has_all_dml_privileges(cursor, role, "public.data_api_grant_probe")


def test_historical_tables_keep_data_api_grants_and_future_tables_are_opt_in():
    connection = psycopg2.connect(os.environ["DB_DSN"])
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            _assert_historical_table_grants(cursor)
            _assert_future_table_has_no_implicit_grants(cursor)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS public.data_api_grant_probe")
        connection.close()
