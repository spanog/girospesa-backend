from __future__ import annotations

import json
import os
import time
import uuid

import httpx
import psycopg2
import psycopg2.extras
import pytest
from jose import jwt


def _db_dsn() -> str:
    return os.environ["DB_DSN"]


def _supabase_url() -> str:
    return os.environ["SUPABASE_URL"]


def _anon_key() -> str:
    return os.environ["SUPABASE_ANON_KEY"]


def _jwt_secret() -> str:
    return os.environ["SUPABASE_INTERNAL_JWT_SECRET"]


def _authenticated_token(user_id: str) -> str:
    now = int(time.time())
    payload = {
        "iss": "supabase",
        "ref": "girospesa-itest",
        "aud": "authenticated",
        "role": "authenticated",
        "sub": user_id,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _set_authenticated_context(cur, user_id: str) -> None:
    cur.execute("SET LOCAL role = authenticated;")
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": user_id, "role": "authenticated"}),),
    )


@pytest.fixture()
def shared_list_context():
    owner_id = str(uuid.uuid4())
    member_id = str(uuid.uuid4())
    outsider_id = str(uuid.uuid4())
    list_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    dsn = _db_dsn()

    conn = psycopg2.connect(dsn)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            for user_id in (owner_id, member_id, outsider_id):
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
                    VALUES (%s, %s, '', NOW(), NOW(), NOW(), '{}'::jsonb, '{}'::jsonb, 'authenticated', 'authenticated')
                    """,
                    (user_id, f"list-rls-{user_id[:8]}@test.local"),
                )

            cur.execute(
                """
                INSERT INTO public.shopping_lists (id, user_id, name, is_active, items)
                VALUES (
                  %s,
                  %s,
                  'Lista condivisa test',
                  true,
                  %s::jsonb
                )
                """,
                (
                    list_id,
                    owner_id,
                    json.dumps(
                        [
                            {
                                "id": item_id,
                                "name": "Pasta",
                                "quantity": 1,
                                "unit": None,
                                "checked": False,
                                "checked_by": None,
                                "checked_at": None,
                                "added_by": owner_id,
                                "added_at": "2026-05-28T07:00:00Z",
                                "source": "manual",
                                "pinned_product_id": None,
                                "pinned_offer_id": None,
                                "found_deals": [],
                            }
                        ]
                    ),
                ),
            )

            cur.execute(
                """
                INSERT INTO public.list_members (list_id, user_id, role)
                VALUES
                  (%s, %s, 'owner'),
                  (%s, %s, 'member')
                """,
                (list_id, owner_id, list_id, member_id),
            )

        conn.commit()
        yield {
            "owner_id": owner_id,
            "member_id": member_id,
            "outsider_id": outsider_id,
            "list_id": list_id,
            "item_id": item_id,
        }
    finally:
        conn.close()
        cleanup = psycopg2.connect(dsn)
        cleanup.autocommit = True
        try:
            with cleanup.cursor() as cur:
                cur.execute("DELETE FROM public.list_members WHERE list_id = %s", (list_id,))
                cur.execute("DELETE FROM public.shopping_lists WHERE id = %s", (list_id,))
                cur.execute(
                    "DELETE FROM auth.users WHERE id = ANY(%s::uuid[])",
                    ([owner_id, member_id, outsider_id],),
                )
        finally:
            cleanup.close()


def test_owner_and_member_keep_rls_access_while_outsider_is_filtered(shared_list_context):
    ctx = shared_list_context
    conn = psycopg2.connect(_db_dsn())
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _set_authenticated_context(cur, ctx["owner_id"])
            cur.execute(
                "SELECT id FROM public.shopping_lists WHERE id = %s",
                (ctx["list_id"],),
            )
            assert cur.fetchall() == [{"id": ctx["list_id"]}]

            cur.execute(
                """
                SELECT user_id, role
                FROM public.list_members
                WHERE list_id = %s
                ORDER BY role, user_id
                """,
                (ctx["list_id"],),
            )
            assert cur.fetchall() == [
                {"user_id": ctx["member_id"], "role": "member"},
                {"user_id": ctx["owner_id"], "role": "owner"},
            ]

            conn.rollback()

            _set_authenticated_context(cur, ctx["member_id"])
            cur.execute(
                "SELECT id FROM public.shopping_lists WHERE id = %s",
                (ctx["list_id"],),
            )
            assert cur.fetchall() == [{"id": ctx["list_id"]}]

            cur.execute(
                "SELECT count(*) AS member_count FROM public.list_members WHERE list_id = %s",
                (ctx["list_id"],),
            )
            assert cur.fetchall() == [{"member_count": 2}]

            conn.rollback()

            _set_authenticated_context(cur, ctx["outsider_id"])
            cur.execute(
                "SELECT id FROM public.shopping_lists WHERE id = %s",
                (ctx["list_id"],),
            )
            assert cur.fetchall() == []

            cur.execute(
                "SELECT user_id FROM public.list_members WHERE list_id = %s",
                (ctx["list_id"],),
            )
            assert cur.fetchall() == []
    finally:
        conn.close()


def test_member_can_still_patch_items_via_public_update_list_item_rpc(shared_list_context):
    ctx = shared_list_context
    conn = psycopg2.connect(_db_dsn())
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _set_authenticated_context(cur, ctx["member_id"])
            cur.execute(
                "SELECT public.update_list_item(%s::uuid, %s::text, %s::jsonb)",
                (
                    ctx["list_id"],
                    ctx["item_id"],
                    json.dumps({"quantity": 4, "checked": True}),
                ),
            )
            conn.commit()

            cur.execute(
                "SELECT items FROM public.shopping_lists WHERE id = %s",
                (ctx["list_id"],),
            )
            row = cur.fetchone()
            item = next(saved for saved in row["items"] if saved["id"] == ctx["item_id"])
            assert item["quantity"] == 4
            assert item["checked"] is True
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_hidden_private_helpers_are_not_exposed_as_public_rpc(shared_list_context):
    token = _authenticated_token(shared_list_context["owner_id"])
    headers = {
        "apikey": _anon_key(),
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url=_supabase_url()) as client:
        member_resp = await client.post(
            "/rest/v1/rpc/is_list_member",
            headers=headers,
            json={
                "p_list_id": shared_list_context["list_id"],
                "p_user_id": shared_list_context["owner_id"],
            },
        )
        owner_resp = await client.post(
            "/rest/v1/rpc/is_list_owner",
            headers=headers,
            json={
                "p_list_id": shared_list_context["list_id"],
                "p_user_id": shared_list_context["owner_id"],
            },
        )

    assert member_resp.status_code == 404
    assert owner_resp.status_code == 404
    assert "is_list_member" in member_resp.text
    assert "is_list_owner" in owner_resp.text
