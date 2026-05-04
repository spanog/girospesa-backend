"""Integration checks for Supabase schema hardening migrations."""

from __future__ import annotations

import os

import psycopg2
import psycopg2.extras


DB_DSN = os.environ["DB_DSN"]


def _fetch_all(query: str) -> list[dict]:
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            return list(cur.fetchall())
    finally:
        conn.close()


def test_internal_tables_have_rls_enabled():
    rows = _fetch_all(
        """
        SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname IN ('analytics_data', 'extraction_log', 'flyer_requests')
        ORDER BY c.relname
        """
    )

    assert rows == [
        {"table_name": "analytics_data", "rls_enabled": True},
        {"table_name": "extraction_log", "rls_enabled": True},
        {"table_name": "flyer_requests", "rls_enabled": True},
    ]


def test_legacy_scraping_log_is_replaced_by_extraction_log():
    rows = _fetch_all(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename IN ('scraping_log', 'extraction_log')
        ORDER BY tablename
        """
    )

    assert rows == [{"tablename": "extraction_log"}]


def test_flyer_requests_has_no_public_insert_policy():
    rows = _fetch_all(
        """
        SELECT policyname, cmd, roles, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'flyer_requests'
        ORDER BY policyname
        """
    )

    assert rows == []


def test_security_definer_functions_have_fixed_public_search_path():
    rows = _fetch_all(
        """
        SELECT p.proname AS function_name,
               COALESCE(array_to_string(p.proconfig, ','), '') AS function_config
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN (
            'products_update_tsv',
            'create_list',
            'update_list_item',
            'offers_compute_fields',
            'set_updated_at'
          )
        ORDER BY p.proname
        """
    )

    assert rows == [
        {"function_name": "create_list", "function_config": "search_path=public"},
        {"function_name": "offers_compute_fields", "function_config": "search_path=public"},
        {"function_name": "products_update_tsv", "function_config": "search_path=public"},
        {"function_name": "set_updated_at", "function_config": "search_path=public"},
        {"function_name": "update_list_item", "function_config": "search_path=public"},
    ]
