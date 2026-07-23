"""Integration checks for Supabase schema hardening migrations."""

from __future__ import annotations

import os
import re
import uuid

import psycopg2
import psycopg2.extras


def _db_dsn() -> str:
    return os.environ["DB_DSN"]


def _fetch_all(query: str) -> list[dict]:
    conn = psycopg2.connect(_db_dsn())
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
          AND c.relname IN (
            'analytics_data',
            'extraction_log',
            'notification_jobs'
          )
        ORDER BY c.relname
        """
    )

    assert rows == [
        {"table_name": "analytics_data", "rls_enabled": True},
        {"table_name": "extraction_log", "rls_enabled": True},
        {"table_name": "notification_jobs", "rls_enabled": True},
    ]


def test_manager_assignment_tables_have_rls_enabled():
    rows = _fetch_all(
        """
        SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname IN ('flyer_targets', 'manager_supermarkets')
        ORDER BY c.relname
        """
    )

    assert rows == [
        {"table_name": "flyer_targets", "rls_enabled": True},
        {"table_name": "manager_supermarkets", "rls_enabled": True},
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
          AND tablename IN (
            'analytics_data',
            'extraction_log',
            'notification_jobs'
          )
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
            "tablename": "notification_jobs",
            "policyname": "notification_jobs_deny_all",
            "cmd": "ALL",
            "roles": ["public"],
            "qual": "false",
            "with_check": "false",
        },
    ]


def test_manager_assignment_tables_have_explicit_deny_all_policies():
    rows = _fetch_all(
        """
        SELECT tablename, policyname, cmd, roles, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename IN ('flyer_targets', 'manager_supermarkets')
        ORDER BY tablename, policyname
        """
    )

    assert rows == [
        {
            "tablename": "flyer_targets",
            "policyname": "flyer_targets_deny_all",
            "cmd": "ALL",
            "roles": ["public"],
            "qual": "false",
            "with_check": "false",
        },
        {
            "tablename": "manager_supermarkets",
            "policyname": "manager_supermarkets_deny_all",
            "cmd": "ALL",
            "roles": ["public"],
            "qual": "false",
            "with_check": "false",
        },
    ]


def test_contact_attachments_bucket_is_removed():
    rows = _fetch_all(
        """
        SELECT id
        FROM storage.buckets
        WHERE id = 'contact-attachments'
        """
    )

    assert rows == []


def test_legacy_flyer_requests_table_is_removed():
    rows = _fetch_all(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename = 'flyer_requests'
        """
    )

    assert rows == []


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
            'merge_shopping_list_items',
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
        {"function_name": "merge_shopping_list_items", "function_config": "search_path=public"},
        {"function_name": "offer_is_currently_active", "function_config": "search_path=public"},
        {"function_name": "offers_compute_fields", "function_config": "search_path=public"},
        {"function_name": "products_update_tsv", "function_config": "search_path=public"},
        {"function_name": "remove_list_item", "function_config": "search_path=public"},
        {"function_name": "search_products_catalog", "function_config": "search_path=public, extensions"},
        {"function_name": "set_updated_at", "function_config": "search_path=public"},
        {"function_name": "update_list_item", "function_config": "search_path=public"},
    ]


def test_private_list_rls_helpers_have_fixed_search_path():
    rows = _fetch_all(
        """
        SELECT p.proname AS function_name,
               COALESCE(array_to_string(p.proconfig, ','), '') AS function_config
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'private'
          AND p.proname IN ('is_list_member', 'is_list_owner')
        ORDER BY p.proname
        """
    )

    assert rows == [
        {"function_name": "is_list_member", "function_config": "search_path=public, pg_temp"},
        {"function_name": "is_list_owner", "function_config": "search_path=public, pg_temp"},
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
            "update_list_item": False,
            "handle_new_user": False,
        },
        {
            "role_name": "authenticated",
            "update_list_item": True,
            "handle_new_user": False,
        },
    ]


def test_private_list_helper_privileges_match_intended_access():
    rows = _fetch_all(
        """
        SELECT
          role_name,
          has_schema_privilege(role_name, 'private', 'USAGE') AS private_usage,
          has_function_privilege(role_name, 'private.is_list_member(uuid, uuid)', 'EXECUTE') AS is_list_member,
          has_function_privilege(role_name, 'private.is_list_owner(uuid, uuid)', 'EXECUTE') AS is_list_owner
        FROM (
          VALUES ('anon'), ('authenticated')
        ) AS roles(role_name)
        ORDER BY role_name
        """
    )

    assert rows == [
        {
            "role_name": "anon",
            "private_usage": False,
            "is_list_member": False,
            "is_list_owner": False,
        },
        {
            "role_name": "authenticated",
            "private_usage": True,
            "is_list_member": True,
            "is_list_owner": True,
        },
    ]


def test_service_role_keeps_public_schema_access_for_backend_queries():
    rows = _fetch_all(
        """
        SELECT
          has_schema_privilege('service_role', 'public', 'USAGE') AS public_usage,
          has_table_privilege('service_role', 'public.supermarkets', 'SELECT') AS supermarkets_select,
          has_table_privilege('service_role', 'public.offers', 'SELECT') AS offers_select,
          has_function_privilege(
            'service_role',
            'public.nearby_supermarkets(double precision, double precision, double precision)',
            'EXECUTE'
          ) AS nearby_supermarkets_execute
        """
    )

    assert rows == [
        {
            "public_usage": True,
            "supermarkets_select": True,
            "offers_select": True,
            "nearby_supermarkets_execute": True,
        }
    ]


def test_public_list_rls_helpers_are_removed():
    rows = _fetch_all(
        """
        SELECT p.proname AS function_name
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('is_list_member', 'is_list_owner')
        ORDER BY p.proname
        """
    )

    assert rows == []


def test_list_rpcs_are_not_security_definer():
    rows = _fetch_all(
        """
        SELECT p.proname AS function_name, p.prosecdef AS security_definer
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname = 'update_list_item'
        """
    )

    assert rows == [
        {"function_name": "update_list_item", "security_definer": False},
    ]


def test_rls_policies_wrap_auth_uid_for_initplan_friendly_execution():
    rows = _fetch_all(
        """
        SELECT tablename, policyname, cmd, COALESCE(qual, '') AS qual, COALESCE(with_check, '') AS with_check
        FROM pg_policies
        WHERE schemaname = 'public'
          AND policyname IN (
            'flyers_auth_read',
            'flyers_auth_insert',
            'flyers_auth_update',
            'flyers_auth_delete',
            'lists_select',
            'lists_insert',
            'lists_update',
            'list_members_select',
            'list_members_insert_owner',
            'list_members_delete_owner',
            'list_invites_select',
            'favorites_own',
            'profiles_own',
            'push_subscriptions_self_manage',
            'Users manage own purchase history',
            'app_notifications_select_self',
            'app_notifications_update_self',
            'offers_auth_read'
          )
        ORDER BY tablename, policyname, cmd
        """
    )

    wrapped_auth_uid = re.compile(
        r"\(\s*select\s+auth\.uid\(\)(?:\s+as\s+\w+)?\s*\)",
        re.IGNORECASE,
    )

    for row in rows:
        expression = " ".join((row["qual"], row["with_check"]))
        normalized = wrapped_auth_uid.sub("", expression)
        assert "auth.uid()" not in normalized, row


def test_push_subscriptions_has_single_authenticated_policy():
    rows = _fetch_all(
        """
        SELECT cmd, COUNT(*) AS policy_count
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'push_subscriptions'
          AND 'authenticated' = ANY(roles::text[])
        GROUP BY cmd
        ORDER BY cmd
        """
    )

    assert rows == [{"cmd": "ALL", "policy_count": 1}]


def test_offers_flyer_draft_product_format_unique_index_is_not_partial():
    rows = _fetch_all(
        """
        SELECT
          i.indisunique AS is_unique,
          pg_get_indexdef(i.indexrelid) AS index_def,
          pg_get_expr(i.indpred, i.indrelid) AS predicate
        FROM pg_index i
        JOIN pg_class idx ON idx.oid = i.indexrelid
        JOIN pg_class tbl ON tbl.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = tbl.relnamespace
        WHERE n.nspname = 'public'
          AND tbl.relname = 'offers'
          AND idx.relname = 'idx_offers_flyer_draft_product_format'
        """
    )

    assert rows == [
        {
            "is_unique": True,
            "index_def": "CREATE UNIQUE INDEX idx_offers_flyer_draft_product_format ON public.offers USING btree (flyer_id, draft_product_key, format_key)",
            "predicate": None,
        }
    ]


def test_auth_signup_trigger_creates_profile_and_default_list():
    user_id = str(uuid.uuid4())
    conn = psycopg2.connect(_db_dsn())
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
                  '{
                    "display_name":"Schema Test",
                    "home_address":"Via Roma 1",
                    "home_city":"Milano",
                    "home_province":"MI",
                    "home_postal_code":"20100"
                  }'::jsonb,
                  'authenticated',
                  'authenticated'
                )
                """,
                (user_id, f"schema-{user_id[:8]}@test.local"),
            )
            conn.commit()

            cur.execute(
                """
                SELECT
                  id,
                  display_name,
                  home_address,
                  home_city,
                  home_province,
                  home_postal_code
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

        assert profile == {
            "id": user_id,
            "display_name": "Schema Test",
            "home_address": "Via Roma 1",
            "home_city": "Milano",
            "home_province": "MI",
            "home_postal_code": "20100",
        }
        assert shopping_list == {
            "user_id": user_id,
            "name": "La mia lista",
            "is_active": True,
            "role": "owner",
        }
    finally:
        cleanup = psycopg2.connect(_db_dsn())
        try:
            cleanup.autocommit = True
            with cleanup.cursor() as cur:
                cur.execute("DELETE FROM auth.users WHERE id = %s", (user_id,))
        finally:
            cleanup.close()
            conn.close()


def test_search_products_catalog_still_returns_matches():
    product_id = str(uuid.uuid4())
    conn = psycopg2.connect(_db_dsn())
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
                  subcategory
                )
                VALUES (
                  %s,
                  'Pasta Integrale',
                  'Barilla',
                  'dispensa',
                  'Primi Piatti e Preparati'
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
        cleanup = psycopg2.connect(_db_dsn())
        try:
            cleanup.autocommit = True
            with cleanup.cursor() as cur:
                cur.execute("DELETE FROM public.products WHERE id = %s", (product_id,))
        finally:
            cleanup.close()
            conn.close()


def test_search_products_catalog_matches_prefix_fragments():
    product_id = str(uuid.uuid4())
    conn = psycopg2.connect(_db_dsn())
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
                  subcategory
                )
                VALUES (
                  %s,
                  'Mozzarella',
                  'Vallelata',
                  'latticini-uova',
                  'Formaggi Freschi'
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
        cleanup = psycopg2.connect(_db_dsn())
        try:
            cleanup.autocommit = True
            with cleanup.cursor() as cur:
                cur.execute("DELETE FROM public.products WHERE id = %s", (product_id,))
        finally:
            cleanup.close()
            conn.close()
