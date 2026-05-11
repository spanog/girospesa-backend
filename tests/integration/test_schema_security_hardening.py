"""Integration checks for Supabase schema hardening migrations."""

from __future__ import annotations

import os
import uuid

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


def test_internal_tables_have_explicit_deny_all_policies():
    rows = _fetch_all(
        """
        SELECT tablename, policyname, cmd, roles, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename IN ('analytics_data', 'extraction_log', 'flyer_requests')
        ORDER BY tablename, policyname
        """
    )

    assert rows == [
        {
            "tablename": "analytics_data",
            "policyname": "analytics_data_deny_all",
            "cmd": "ALL",
            "roles": ["public"],
            "qual": "false",
            "with_check": "false",
        },
        {
            "tablename": "extraction_log",
            "policyname": "extraction_log_deny_all",
            "cmd": "ALL",
            "roles": ["public"],
            "qual": "false",
            "with_check": "false",
        },
        {
            "tablename": "flyer_requests",
            "policyname": "flyer_requests_deny_all",
            "cmd": "ALL",
            "roles": ["public"],
            "qual": "false",
            "with_check": "false",
        },
    ]


def test_security_sensitive_functions_have_fixed_search_path():
    rows = _fetch_all(
        """
        SELECT p.proname AS function_name,
               COALESCE(array_to_string(p.proconfig, ','), '') AS function_config
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN (
            'append_list_item',
            'create_list',
            'products_update_tsv',
            'remove_list_item',
            'search_products_catalog',
            'offer_is_currently_active',
            'offers_compute_fields',
            'set_updated_at',
            'update_list_item'
          )
        ORDER BY p.proname
        """
    )

    assert rows == [
        {"function_name": "append_list_item", "function_config": "search_path=public"},
        {"function_name": "create_list", "function_config": "search_path=public"},
        {"function_name": "offer_is_currently_active", "function_config": "search_path=public"},
        {"function_name": "offers_compute_fields", "function_config": "search_path=public"},
        {"function_name": "products_update_tsv", "function_config": "search_path=public"},
        {"function_name": "remove_list_item", "function_config": "search_path=public"},
        {"function_name": "search_products_catalog", "function_config": "search_path=public, extensions"},
        {"function_name": "set_updated_at", "function_config": "search_path=public"},
        {"function_name": "update_list_item", "function_config": "search_path=public"},
    ]


def test_public_extensions_live_in_extensions_schema():
    rows = _fetch_all(
        """
        SELECT extname AS extension_name, extnamespace::regnamespace::text AS schema_name
        FROM pg_extension
        WHERE extname IN ('citext', 'pg_trgm')
        ORDER BY extname
        """
    )

    assert rows == [
        {"extension_name": "citext", "schema_name": "extensions"},
        {"extension_name": "pg_trgm", "schema_name": "extensions"},
    ]


def test_graphql_extension_is_disabled():
    rows = _fetch_all(
        """
        SELECT extname
        FROM pg_extension
        WHERE extname = 'pg_graphql'
        """
    )

    assert rows == []


def test_public_storage_buckets_do_not_allow_listing():
    rows = _fetch_all(
        """
        SELECT policyname
        FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname IN (
            'avatars_read_public',
            'logos_read_public',
            'product_images_read_public'
          )
        ORDER BY policyname
        """
    )

    assert rows == []


def test_list_rpc_execute_privileges_match_intended_access():
    rows = _fetch_all(
        """
        SELECT
          role_name,
          has_function_privilege(role_name, 'public.create_list(text)', 'EXECUTE') AS create_list,
          has_function_privilege(role_name, 'public.update_list_item(uuid, text, jsonb)', 'EXECUTE') AS update_list_item,
          has_function_privilege(role_name, 'public.handle_new_user()', 'EXECUTE') AS handle_new_user
        FROM (
          VALUES ('anon'), ('authenticated')
        ) AS roles(role_name)
        ORDER BY role_name
        """
    )

    assert rows == [
        {
            "role_name": "anon",
            "create_list": False,
            "update_list_item": False,
            "handle_new_user": False,
        },
        {
            "role_name": "authenticated",
            "create_list": True,
            "update_list_item": True,
            "handle_new_user": False,
        },
    ]


def test_list_rpcs_are_not_security_definer():
    rows = _fetch_all(
        """
        SELECT p.proname AS function_name, p.prosecdef AS security_definer
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('create_list', 'update_list_item')
        ORDER BY p.proname
        """
    )

    assert rows == [
        {"function_name": "create_list", "security_definer": False},
        {"function_name": "update_list_item", "security_definer": False},
    ]


def test_auth_signup_trigger_creates_profile_and_default_list():
    user_id = str(uuid.uuid4())
    conn = psycopg2.connect(DB_DSN)
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO auth.users (
                  id,
                  email,
                  encrypted_password,
                  email_confirmed_at,
                  created_at,
                  updated_at,
                  raw_app_meta_data,
                  raw_user_meta_data,
                  aud,
                  role
                )
                VALUES (
                  %s,
                  %s,
                  '',
                  NOW(),
                  NOW(),
                  NOW(),
                  '{}'::jsonb,
                  '{"display_name":"Schema Test"}'::jsonb,
                  'authenticated',
                  'authenticated'
                )
                """,
                (user_id, f"schema-{user_id[:8]}@test.local"),
            )
            conn.commit()

            cur.execute(
                """
                SELECT id, display_name
                FROM public.user_profiles
                WHERE id = %s
                """,
                (user_id,),
            )
            profile = cur.fetchone()

            cur.execute(
                """
                SELECT sl.user_id, sl.name, sl.is_active, lm.role
                FROM public.shopping_lists sl
                JOIN public.list_members lm
                  ON lm.list_id = sl.id
                 AND lm.user_id = sl.user_id
                WHERE sl.user_id = %s
                """,
                (user_id,),
            )
            shopping_list = cur.fetchone()

        assert profile == {"id": user_id, "display_name": "Schema Test"}
        assert shopping_list == {
            "user_id": user_id,
            "name": "Lista principale",
            "is_active": True,
            "role": "owner",
        }
    finally:
        cleanup = psycopg2.connect(DB_DSN)
        try:
            cleanup.autocommit = True
            with cleanup.cursor() as cur:
                cur.execute("DELETE FROM auth.users WHERE id = %s", (user_id,))
        finally:
            cleanup.close()
            conn.close()


def test_search_products_catalog_still_returns_matches():
    product_id = str(uuid.uuid4())
    conn = psycopg2.connect(DB_DSN)
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO public.products (
                  id,
                  name,
                  brand,
                  category,
                  subcategory,
                  format,
                  format_key,
                  format_label
                )
                VALUES (
                  %s,
                  'Pasta Integrale',
                  'Barilla',
                  'dispensa',
                  'Primi Piatti e Preparati',
                  '{"tipo":"pezzo"}'::jsonb,
                  'tipo:pezzo',
                  'Pezzo'
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (product_id,),
            )
            conn.commit()

            cur.execute(
                """
                SELECT id, name, brand
                FROM public.search_products_catalog('barill', 5)
                WHERE id = %s
                """,
                (product_id,),
            )
            match = cur.fetchone()

        assert match == {
            "id": product_id,
            "name": "Pasta Integrale",
            "brand": "Barilla",
        }
    finally:
        cleanup = psycopg2.connect(DB_DSN)
        try:
            cleanup.autocommit = True
            with cleanup.cursor() as cur:
                cur.execute("DELETE FROM public.products WHERE id = %s", (product_id,))
        finally:
            cleanup.close()
            conn.close()


def test_search_products_catalog_matches_prefix_fragments():
    product_id = str(uuid.uuid4())
    conn = psycopg2.connect(DB_DSN)
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO public.products (
                  id,
                  name,
                  brand,
                  category,
                  subcategory,
                  format,
                  format_key,
                  format_label
                )
                VALUES (
                  %s,
                  'Mozzarella',
                  'Vallelata',
                  'latticini-uova',
                  'Formaggi Freschi',
                  '{"tipo":"pezzo"}'::jsonb,
                  'tipo:pezzo',
                  'Pezzo'
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (product_id,),
            )
            conn.commit()

            cur.execute(
                """
                SELECT id, name, brand
                FROM public.search_products_catalog('mozza', 100)
                WHERE id = %s
                """,
                (product_id,),
            )
            match = cur.fetchone()

        assert match == {
            "id": product_id,
            "name": "Mozzarella",
            "brand": "Vallelata",
        }
    finally:
        cleanup = psycopg2.connect(DB_DSN)
        try:
            cleanup.autocommit = True
            with cleanup.cursor() as cur:
                cur.execute("DELETE FROM public.products WHERE id = %s", (product_id,))
        finally:
            cleanup.close()
            conn.close()
