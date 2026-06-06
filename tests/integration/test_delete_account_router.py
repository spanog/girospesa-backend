from __future__ import annotations

import os
import time
import uuid

import psycopg2
from jose import jwt

from tests.conftest import wait_for_user_bootstrap


def _session_token(user_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": f"delete-{user_id[:8]}@test.local",
        "role": "customer",
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, os.environ["APP_SESSION_SECRET"], algorithm="HS256")


def _row_count(query: str, value: str) -> int:
    conn = psycopg2.connect(os.environ["DB_DSN"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(query, (value,))
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def _wait_for_row_count(query: str, value: str, expected: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _row_count(query, value) == expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"Expected row count {expected} for {value}")


def _scalar_value(query: str, value: str):
    conn = psycopg2.connect(os.environ["DB_DSN"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(query, (value,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


async def test_delete_me_removes_auth_user_and_related_rows(
    async_client,
    clean_db,
    supabase_client,
):
    from main import app

    route = next(
        item for item in app.routes
        if item.path == "/users/me" and "DELETE" in getattr(item, "methods", set())
    )
    original_delete_auth_user = route.endpoint.__globals__["_delete_auth_user"]
    route.endpoint.__globals__["_delete_auth_user"] = (
        lambda user_id: supabase_client.auth.admin.delete_user(user_id)
    )

    try:
        email = f"delete_me_{uuid.uuid4().hex[:8]}@test.local"
        user = supabase_client.auth.admin.create_user(
            {"email": email, "password": "Test_password_123!", "email_confirm": True}
        ).user
        wait_for_user_bootstrap(user.id)

        product = supabase_client.table("products").insert(
            {"name": f"Prodotto {uuid.uuid4().hex[:6]}", "brand": "TestBrand"}
        ).execute().data[0]
        supabase_client.table("favorites").insert(
            {"user_id": user.id, "product_id": product["id"]}
        ).execute()

        response = await async_client.delete(
            "/users/me",
            cookies={"girospesa_session": _session_token(user.id)},
            headers={"Origin": "http://127.0.0.1:3000"},
        )

        assert response.status_code == 204, response.text
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
        _wait_for_row_count("SELECT count(*) FROM auth.users WHERE id = %s", user.id, 0)
        _wait_for_row_count("SELECT count(*) FROM public.user_profiles WHERE id = %s", user.id, 0)
        _wait_for_row_count("SELECT count(*) FROM public.favorites WHERE user_id = %s", user.id, 0)
    finally:
        route.endpoint.__globals__["_delete_auth_user"] = original_delete_auth_user


async def test_delete_me_cleans_invite_references_before_auth_delete(
    async_client,
    clean_db,
    supabase_client,
):
    from main import app

    route = next(
        item for item in app.routes
        if item.path == "/users/me" and "DELETE" in getattr(item, "methods", set())
    )
    original_delete_auth_user = route.endpoint.__globals__["_delete_auth_user"]
    route.endpoint.__globals__["_delete_auth_user"] = (
        lambda user_id: supabase_client.auth.admin.delete_user(user_id)
    )

    try:
        owner = supabase_client.auth.admin.create_user(
            {
                "email": f"owner_{uuid.uuid4().hex[:8]}@test.local",
                "password": "Test_password_123!",
                "email_confirm": True,
            }
        ).user
        member = supabase_client.auth.admin.create_user(
            {
                "email": f"member_{uuid.uuid4().hex[:8]}@test.local",
                "password": "Test_password_123!",
                "email_confirm": True,
            }
        ).user
        wait_for_user_bootstrap(owner.id)
        wait_for_user_bootstrap(member.id)

        shopping_list = supabase_client.table("shopping_lists").insert(
            {
                "user_id": owner.id,
                "name": "Lista inviti test",
                "items": [],
                "is_active": True,
            }
        ).execute().data[0]
        supabase_client.table("list_members").insert(
            {
                "list_id": shopping_list["id"],
                "user_id": owner.id,
                "role": "owner",
            }
        ).execute()
        invite = supabase_client.table("list_invites").insert(
            {
                "list_id": shopping_list["id"],
                "invited_by": owner.id,
                "invited_user_id": member.id,
                "email": f"member-{uuid.uuid4().hex[:6]}@test.local",
                "status": "accepted",
                "accepted_by": member.id,
                "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        ).execute().data[0]

        response = await async_client.delete(
            "/users/me",
            cookies={"girospesa_session": _session_token(member.id)},
            headers={"Origin": "http://127.0.0.1:3000"},
        )

        assert response.status_code == 204, response.text
        _wait_for_row_count("SELECT count(*) FROM auth.users WHERE id = %s", member.id, 0)
        remaining_invites = _row_count(
            "SELECT count(*) FROM public.list_invites WHERE id = %s",
            invite["id"],
        )
        assert remaining_invites in {0, 1}
        if remaining_invites == 1:
            assert (
                _scalar_value(
                    "SELECT accepted_by FROM public.list_invites WHERE id = %s",
                    invite["id"],
                )
                is None
            )
    finally:
        route.endpoint.__globals__["_delete_auth_user"] = original_delete_auth_user
