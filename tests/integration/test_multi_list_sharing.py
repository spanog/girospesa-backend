from __future__ import annotations

import os
import psycopg2
import psycopg2.extras
import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.lists import router as lists_router
from api.routers.notifications import router as notifications_router
from core.auth import get_current_user_id
from tests.conftest import wait_for_user_bootstrap
from tests.snapshot_utils import assert_matches_json_snapshot

app = FastAPI()
app.include_router(lists_router, prefix="/lists")
app.include_router(notifications_router, prefix="/notifications")

def _db_dsn() -> str | None:
    return os.getenv("DB_DSN")


def _ensure_auth_user_row(user_id: str, email: str) -> None:
    dsn = _db_dsn()
    if not dsn:
        return
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
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
        ON CONFLICT (id) DO NOTHING
        """,
        (user_id, email),
    )
    conn.close()


def _db_fetch_one(query: str, params: tuple) -> dict | None:
    dsn = _db_dsn()
    if not dsn:
        return None
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(query, params)
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _db_fetch_all(query: str, params: tuple) -> list[dict]:
    dsn = _db_dsn()
    if not dsn:
        return []
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(query, params)
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _set_profile_display_name(user_id: str, display_name: str) -> None:
    dsn = _db_dsn()
    if not dsn:
        return
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE public.user_profiles
        SET display_name = %s
        WHERE id = %s
        """,
        (display_name, user_id),
    )
    conn.close()


def _set_notifications_enabled(user_id: str, enabled: bool) -> None:
    dsn = _db_dsn()
    if not dsn:
        return
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE public.user_profiles
        SET notifications_enabled = %s
        WHERE id = %s
        """,
        (enabled, user_id),
    )
    conn.close()


def _insert_push_subscription(user_id: str) -> None:
    dsn = _db_dsn()
    if not dsn:
        return
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO public.push_subscriptions (user_id, endpoint, p256dh, auth_key, user_agent)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            user_id,
            f"https://push.example.com/{uuid.uuid4().hex}",
            "test_p256dh_key",
            "test_auth_key",
            "TestBrowser/1.0",
        ),
    )
    conn.close()


@pytest.fixture()
def owner_user(supabase_client):
    email = f"owner_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    _ensure_auth_user_row(user_id, email)
    wait_for_user_bootstrap(user_id)
    _set_profile_display_name(user_id, "Owner Test")
    supabase_client.table("user_profiles").update(
        {"display_name": "Owner Test"}
    ).eq("id", user_id).execute()
    yield {"id": user_id, "email": email}
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def member_user(supabase_client):
    email = f"member_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    _ensure_auth_user_row(user_id, email)
    wait_for_user_bootstrap(user_id)
    _set_profile_display_name(user_id, "Member Test")
    supabase_client.table("user_profiles").update(
        {"display_name": "Member Test"}
    ).eq("id", user_id).execute()
    yield {"id": user_id, "email": email}
    supabase_client.auth.admin.delete_user(user_id)


async def _client_as(user_id: str):
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return httpx.AsyncClient(app=app, base_url="http://test")


@pytest.mark.asyncio
async def test_user_can_create_second_list_and_switch_selection(
    supabase_client, owner_user, clean_db, request
):
    async with await _client_as(owner_user["id"]) as client:
        active_resp = await client.get("/lists/active")
        assert active_resp.status_code == 200
        default_list = active_resp.json()
        assert default_list["is_default"] is True
        assert_matches_json_snapshot(
            request, "lists_active_default_owner", default_list
        )

        create_resp = await client.post("/lists", json={"name": "Weekend"})
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["is_default"] is False
        assert created["is_selected"] is True

        list_resp = await client.get("/lists")
        assert list_resp.status_code == 200
        lists = list_resp.json()
        assert len(lists) >= 2
        assert any(row["id"] == created["id"] and row["is_selected"] for row in lists)

        select_resp = await client.post(
            "/lists/select", json={"list_id": default_list["id"]}
        )
        assert select_resp.status_code == 200
        assert select_resp.json()["id"] == default_list["id"]

        delete_default_resp = await client.delete(f"/lists/{default_list['id']}")
        assert delete_default_resp.status_code == 400

        rename_default_resp = await client.patch(
            f"/lists/{default_list['id']}",
            json={"name": "Spesa casa"},
        )
        assert rename_default_resp.status_code == 400

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_email_invite_creates_notification_and_accept_flow(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        active_resp = await owner_client.get("/lists/active")
        owner_list = active_resp.json()

        invite_resp = await owner_client.post(
            f"/lists/{owner_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()
        assert invite["invited_user_id"] == member_user["id"]

    async with await _client_as(member_user["id"]) as member_client:
        notifications_resp = await member_client.get("/notifications")
        assert notifications_resp.status_code == 200
        notifications = notifications_resp.json()
        assert any(row["kind"] == "list_invite" for row in notifications)

        pending_resp = await member_client.get("/lists/invites/pending")
        assert pending_resp.status_code == 200
        pending = pending_resp.json()
        assert len(pending) == 1
        assert pending[0]["id"] == invite["id"]

        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept", json={}
        )
        assert accept_resp.status_code == 200
        assert accept_resp.json()["list_id"] == owner_list["id"]

    invite_row = _db_fetch_one(
        """
        SELECT status, accepted_by
        FROM public.list_invites
        WHERE id = %s
        """,
        (invite["id"],),
    )
    assert invite_row is not None
    assert invite_row["status"] == "accepted"
    assert invite_row["accepted_by"] == member_user["id"]


@pytest.mark.asyncio
async def test_received_invites_endpoint_includes_closed_statuses(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        active_resp = await owner_client.get("/lists/active")
        owner_list = active_resp.json()

        invite_resp = await owner_client.post(
            f"/lists/{owner_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        decline_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/decline", json={}
        )
        assert decline_resp.status_code == 204

        invites_resp = await member_client.get("/lists/invites")
        assert invites_resp.status_code == 200
        invites = invites_resp.json()

    assert len(invites) == 1
    assert invites[0]["id"] == invite["id"]
    assert invites[0]["status"] == "declined"
    assert invites[0]["list_name"] == owner_list["name"]
    assert invites[0]["invited_by_name"] == "Owner Test"

    notification_rows = _db_fetch_all(
        """
        SELECT read_at
        FROM public.app_notifications
        WHERE user_id = %s
          AND kind = 'list_invite'
        """,
        (member_user["id"],),
    )
    assert notification_rows
    assert notification_rows[0]["read_at"] is not None


@pytest.mark.asyncio
async def test_revoked_invite_keeps_inbox_history_and_returns_conflict(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        active_resp = await owner_client.get("/lists/active")
        owner_list = active_resp.json()

        invite_resp = await owner_client.post(
            f"/lists/{owner_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

        revoke_resp = await owner_client.delete(
            f"/lists/{owner_list['id']}/invites/{invite['id']}"
        )
        assert revoke_resp.status_code == 204

    async with await _client_as(member_user["id"]) as member_client:
        notifications_resp = await member_client.get("/notifications")
        assert notifications_resp.status_code == 200
        notifications = notifications_resp.json()
        invite_notification = next(
            row
            for row in notifications
            if row["kind"] == "list_invite"
            and row["data"].get("invite_id") == invite["id"]
        )
        assert invite_notification["title"] == "Invito revocato"
        assert invite_notification["data"]["invite_status"] == "revoked"

        pending_resp = await member_client.get("/lists/invites/pending")
        assert pending_resp.status_code == 200
        assert pending_resp.json() == []

        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept",
            json={},
        )
        assert accept_resp.status_code == 409
        assert accept_resp.json()["detail"] == "Invite has been revoked"

        decline_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/decline",
            json={},
        )
        assert decline_resp.status_code == 409
        assert decline_resp.json()["detail"] == "Invite has been revoked"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_non_owner_member_cannot_manage_sharing(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        create_resp = await owner_client.post("/lists", json={"name": "Weekend"})
        assert create_resp.status_code == 201
        shared_list = create_resp.json()

        invite_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept",
            json={},
        )
        assert accept_resp.status_code == 200

        create_direct_resp = await member_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": owner_user["email"]},
        )
        assert create_direct_resp.status_code == 403
        assert (
            create_direct_resp.json()["detail"]
            == "Only the owner can perform this action"
        )

        create_legacy_resp = await member_client.post(
            f"/lists/{shared_list['id']}/invite",
            json={},
        )
        assert create_legacy_resp.status_code == 403
        assert (
            create_legacy_resp.json()["detail"]
            == "Only the owner can perform this action"
        )

        list_invites_resp = await member_client.get(
            f"/lists/{shared_list['id']}/invites"
        )
        assert list_invites_resp.status_code == 403
        assert (
            list_invites_resp.json()["detail"]
            == "Only the owner can perform this action"
        )

        revoke_resp = await member_client.delete(
            f"/lists/{shared_list['id']}/invites/{invite['id']}"
        )
        assert revoke_resp.status_code == 403
        assert revoke_resp.json()["detail"] == "Only the owner can perform this action"

    app.dependency_overrides.clear()

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_decline_invite_marks_invite_declined_without_membership(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        owner_list = (await owner_client.get("/lists/active")).json()
        invite_resp = await owner_client.post(
            f"/lists/{owner_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        decline_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/decline", json={}
        )
        assert decline_resp.status_code == 204

    invite_row = _db_fetch_one(
        """
        SELECT status, declined_at
        FROM public.list_invites
        WHERE id = %s
        """,
        (invite["id"],),
    )
    assert invite_row is not None
    assert invite_row["status"] == "declined"
    assert invite_row["declined_at"] is not None

    member_rows = (
        supabase_client.table("list_members")
        .select("id")
        .eq("list_id", owner_list["id"])
        .eq("user_id", member_user["id"])
        .execute()
        .data
    )
    assert member_rows == []

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_owner_delete_shared_list_notifies_members_and_falls_back_selected_list(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        default_list = (await owner_client.get("/lists/active")).json()
        create_resp = await owner_client.post("/lists", json={"name": "Weekend"})
        assert create_resp.status_code == 201
        shared_list = create_resp.json()

        invite_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept",
            json={},
        )
        assert accept_resp.status_code == 200

        select_resp = await member_client.post(
            "/lists/select",
            json={"list_id": shared_list["id"]},
        )
        assert select_resp.status_code == 200

    _insert_push_subscription(member_user["id"])

    with patch("api.routers.lists.send_push_notification") as mock_push:
        async with await _client_as(owner_user["id"]) as owner_client:
            delete_resp = await owner_client.delete(f"/lists/{shared_list['id']}")
            assert delete_resp.status_code == 204

    deleted_rows = (
        supabase_client.table("shopping_lists")
        .select("id")
        .eq("id", shared_list["id"])
        .execute()
        .data
    )
    assert deleted_rows == []

    profile_row = _db_fetch_one(
        """
        SELECT active_list_id
        FROM public.user_profiles
        WHERE id = %s
        """,
        (member_user["id"],),
    )
    assert profile_row is not None
    member_default = _db_fetch_one(
        """
        SELECT id
        FROM public.shopping_lists
        WHERE user_id = %s
          AND is_default = true
        LIMIT 1
        """,
        (member_user["id"],),
    )
    assert member_default is not None
    assert profile_row["active_list_id"] == member_default["id"]

    notification_rows = _db_fetch_all(
        """
        SELECT kind, title, body, data
        FROM public.app_notifications
        WHERE user_id = %s
          AND kind = 'list_deleted'
        """,
        (member_user["id"],),
    )
    assert len(notification_rows) == 1
    notification = notification_rows[0]
    assert notification["title"] == "Lista rimossa"
    assert notification["data"]["list_id"] == shared_list["id"]
    assert notification["data"]["list_name"] == shared_list["name"]
    assert notification["data"]["deleted_by"] == "Owner Test"
    assert notification["data"]["url"] == "/lista"
    assert "Weekend" in notification["body"]

    owner_notifications = _db_fetch_all(
        """
        SELECT id
        FROM public.app_notifications
        WHERE user_id = %s
          AND kind = 'list_deleted'
        """,
        (owner_user["id"],),
    )
    assert owner_notifications == []

    async with await _client_as(owner_user["id"]) as owner_client:
        active_after_delete = await owner_client.get("/lists/active")
        assert active_after_delete.status_code == 200
        assert active_after_delete.json()["id"] == default_list["id"]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_member_cannot_delete_owner_list(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        shared_list = (await owner_client.get("/lists/active")).json()
        invite_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept",
            json={},
        )
        assert accept_resp.status_code == 200

        delete_resp = await member_client.delete(f"/lists/{shared_list['id']}")
        assert delete_resp.status_code == 403

    remaining_rows = _db_fetch_all(
        """
        SELECT id
        FROM public.shopping_lists
        WHERE id = %s
        """,
        (shared_list["id"],),
    )
    assert len(remaining_rows) == 1

    member_notifications = (
        supabase_client.table("app_notifications")
        .select("id")
        .eq("user_id", member_user["id"])
        .eq("kind", "list_deleted")
        .execute()
        .data
    )
    assert member_notifications == []

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_owner_remove_member_notifies_target_and_falls_back_selected_list(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        create_resp = await owner_client.post("/lists", json={"name": "Weekend"})
        assert create_resp.status_code == 201
        shared_list = create_resp.json()

        invite_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept",
            json={},
        )
        assert accept_resp.status_code == 200

        select_resp = await member_client.post(
            "/lists/select",
            json={"list_id": shared_list["id"]},
        )
        assert select_resp.status_code == 200

    _insert_push_subscription(member_user["id"])

    with patch("api.routers.lists.send_push_notification") as mock_push:
        async with await _client_as(owner_user["id"]) as owner_client:
            remove_resp = await owner_client.delete(
                f"/lists/{shared_list['id']}/members/{member_user['id']}"
            )
            assert remove_resp.status_code == 204

    member_rows = (
        supabase_client.table("list_members")
        .select("id")
        .eq("list_id", shared_list["id"])
        .eq("user_id", member_user["id"])
        .execute()
        .data
    )
    assert member_rows == []

    profile_row = _db_fetch_one(
        """
        SELECT active_list_id
        FROM public.user_profiles
        WHERE id = %s
        """,
        (member_user["id"],),
    )
    assert profile_row is not None
    member_default = _db_fetch_one(
        """
        SELECT id
        FROM public.shopping_lists
        WHERE user_id = %s
          AND is_default = true
        LIMIT 1
        """,
        (member_user["id"],),
    )
    assert member_default is not None
    assert profile_row["active_list_id"] == member_default["id"]

    notification_rows = _db_fetch_all(
        """
        SELECT kind, title, body, data
        FROM public.app_notifications
        WHERE user_id = %s
          AND kind = 'list_member_removed'
        """,
        (member_user["id"],),
    )
    assert len(notification_rows) == 1
    notification = notification_rows[0]
    assert notification["title"] == "Rimosso dalla lista"
    assert notification["data"]["list_id"] == shared_list["id"]
    assert notification["data"]["list_name"] == shared_list["name"]
    assert notification["data"]["removed_by"] == "Owner Test"
    assert notification["data"]["removed_by_email"] == owner_user["email"]
    assert notification["data"]["url"] == "/lista"
    assert "Owner Test" in notification["body"]
    assert f"({owner_user['email']})" in notification["body"]
    assert "Weekend" in notification["body"]

    owner_notifications = _db_fetch_all(
        """
        SELECT id
        FROM public.app_notifications
        WHERE user_id = %s
          AND kind = 'list_member_removed'
        """,
        (owner_user["id"],),
    )
    assert owner_notifications == []

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_shared_list_notifications_respect_profile_preference(
    supabase_client, owner_user, member_user, clean_db
):
    _set_notifications_enabled(member_user["id"], False)

    async with await _client_as(owner_user["id"]) as owner_client:
        create_resp = await owner_client.post("/lists", json={"name": "Weekend"})
        assert create_resp.status_code == 201
        shared_list = create_resp.json()

        with patch("api.routers.lists.send_push_notification") as mock_push:
            invite_resp = await owner_client.post(
                f"/lists/{shared_list['id']}/invites",
                json={"email": member_user["email"]},
            )

    assert invite_resp.status_code == 201
    assert invite_resp.json()["notification"] is None
    mock_push.assert_not_called()

    notification_rows = _db_fetch_all(
        """
        SELECT id
        FROM public.app_notifications
        WHERE user_id = %s
          AND kind = 'list_invite'
        """,
        (member_user["id"],),
    )
    assert notification_rows == []

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_member_can_leave_shared_list_and_owner_gets_notification(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        create_resp = await owner_client.post("/lists", json={"name": "Weekend"})
        assert create_resp.status_code == 201
        shared_list = create_resp.json()

        invite_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept",
            json={},
        )
        assert accept_resp.status_code == 200

        select_resp = await member_client.post(
            "/lists/select",
            json={"list_id": shared_list["id"]},
        )
        assert select_resp.status_code == 200

    _insert_push_subscription(owner_user["id"])

    with patch("api.routers.lists.send_push_notification") as mock_push:
        async with await _client_as(member_user["id"]) as member_client:
            leave_resp = await member_client.delete(
                f"/lists/{shared_list['id']}/members/{member_user['id']}"
            )
            assert leave_resp.status_code == 204

    member_rows = (
        supabase_client.table("list_members")
        .select("id")
        .eq("list_id", shared_list["id"])
        .eq("user_id", member_user["id"])
        .execute()
        .data
    )
    assert member_rows == []

    profile_row = _db_fetch_one(
        """
        SELECT active_list_id
        FROM public.user_profiles
        WHERE id = %s
        """,
        (member_user["id"],),
    )
    assert profile_row is not None
    member_default = _db_fetch_one(
        """
        SELECT id
        FROM public.shopping_lists
        WHERE user_id = %s
          AND is_default = true
        LIMIT 1
        """,
        (member_user["id"],),
    )
    assert member_default is not None
    assert profile_row["active_list_id"] == member_default["id"]

    owner_notifications = _db_fetch_all(
        """
        SELECT kind, title, body, data
        FROM public.app_notifications
        WHERE user_id = %s
          AND kind = 'list_member_left'
        """,
        (owner_user["id"],),
    )
    assert len(owner_notifications) == 1
    notification = owner_notifications[0]
    assert notification["title"] == "Membro uscito dalla lista"
    assert notification["data"]["list_id"] == shared_list["id"]
    assert notification["data"]["list_name"] == shared_list["name"]
    assert notification["data"]["left_by"] == "Member Test"
    assert notification["data"]["left_by_email"] == member_user["email"]
    assert notification["data"]["url"] == "/lista"
    assert "Member Test" in notification["body"]
    assert f"({member_user['email']})" in notification["body"]
    assert "Weekend" in notification["body"]

    member_notifications = _db_fetch_all(
        """
        SELECT id
        FROM public.app_notifications
        WHERE user_id = %s
          AND kind = 'list_member_left'
        """,
        (member_user["id"],),
    )
    assert member_notifications == []

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_non_owner_cannot_remove_member(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        shared_list = (await owner_client.get("/lists/active")).json()
        invite_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept",
            json={},
        )
        assert accept_resp.status_code == 200

        remove_resp = await member_client.delete(
            f"/lists/{shared_list['id']}/members/{owner_user['id']}"
        )
        assert remove_resp.status_code == 403

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_owner_cannot_leave_own_list(
    supabase_client, owner_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        shared_list = (await owner_client.get("/lists/active")).json()

        leave_resp = await owner_client.delete(
            f"/lists/{shared_list['id']}/members/{owner_user['id']}"
        )
        assert leave_resp.status_code == 400

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_owner_remove_member_returns_404_when_target_missing(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        shared_list = (await owner_client.get("/lists/active")).json()

        remove_resp = await owner_client.delete(
            f"/lists/{shared_list['id']}/members/{member_user['id']}"
        )
        assert remove_resp.status_code == 404

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_stranger_cannot_view_or_modify_unshared_list(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        shared_list = (await owner_client.get("/lists/active")).json()
        add_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/items",
            json={"name": "Latte", "quantity": 1},
        )
        assert add_resp.status_code == 201
        item_id = add_resp.json()["id"]

    async with await _client_as(member_user["id"]) as stranger_client:
        lists_resp = await stranger_client.get("/lists")
        assert lists_resp.status_code == 200
        assert shared_list["id"] not in {row["id"] for row in lists_resp.json()}

        get_resp = await stranger_client.get(f"/lists/{shared_list['id']}")
        assert get_resp.status_code == 403

        members_resp = await stranger_client.get(f"/lists/{shared_list['id']}/members")
        assert members_resp.status_code == 403

        freshness_resp = await stranger_client.get(
            f"/lists/{shared_list['id']}/deal-freshness"
        )
        assert freshness_resp.status_code == 403

        reset_resp = await stranger_client.post(f"/lists/{shared_list['id']}/reset", json={})
        assert reset_resp.status_code == 403

        add_resp = await stranger_client.post(
            f"/lists/{shared_list['id']}/items",
            json={"name": "Pane", "quantity": 1},
        )
        assert add_resp.status_code == 403

        delete_resp = await stranger_client.delete(
            f"/lists/{shared_list['id']}/items/{item_id}"
        )
        assert delete_resp.status_code == 403

        toggle_resp = await stranger_client.post(
            f"/lists/{shared_list['id']}/items/{item_id}/toggle",
            json={},
        )
        assert toggle_resp.status_code == 403

        check_resp = await stranger_client.post(
            f"/lists/{shared_list['id']}/items/{item_id}/check",
            json={"checked": True},
        )
        assert check_resp.status_code == 403

        patch_resp = await stranger_client.patch(
            f"/lists/{shared_list['id']}/items/{item_id}",
            json={"quantity": 2},
        )
        assert patch_resp.status_code == 403

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_pending_invite_does_not_grant_list_access(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        shared_list = (await owner_client.get("/lists/active")).json()
        invite_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        pending_resp = await member_client.get("/lists/invites/pending")
        assert pending_resp.status_code == 200
        assert {row["id"] for row in pending_resp.json()} == {invite["id"]}

        lists_resp = await member_client.get("/lists")
        assert lists_resp.status_code == 200
        assert shared_list["id"] not in {row["id"] for row in lists_resp.json()}

        get_resp = await member_client.get(f"/lists/{shared_list['id']}")
        assert get_resp.status_code == 403

        add_resp = await member_client.post(
            f"/lists/{shared_list['id']}/items",
            json={"name": "Pasta", "quantity": 1},
        )
        assert add_resp.status_code == 403

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_removed_member_loses_access_immediately(
    supabase_client, owner_user, member_user, clean_db
):
    async with await _client_as(owner_user["id"]) as owner_client:
        create_resp = await owner_client.post("/lists", json={"name": "Weekend"})
        assert create_resp.status_code == 201
        shared_list = create_resp.json()

        invite_resp = await owner_client.post(
            f"/lists/{shared_list['id']}/invites",
            json={"email": member_user["email"]},
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()

    async with await _client_as(member_user["id"]) as member_client:
        accept_resp = await member_client.post(
            f"/lists/invites/{invite['id']}/accept",
            json={},
        )
        assert accept_resp.status_code == 200

        get_resp = await member_client.get(f"/lists/{shared_list['id']}")
        assert get_resp.status_code == 200

    async with await _client_as(owner_user["id"]) as owner_client:
        remove_resp = await owner_client.delete(
            f"/lists/{shared_list['id']}/members/{member_user['id']}"
        )
        assert remove_resp.status_code == 204

    async with await _client_as(member_user["id"]) as former_member_client:
        lists_resp = await former_member_client.get("/lists")
        assert lists_resp.status_code == 200
        assert shared_list["id"] not in {row["id"] for row in lists_resp.json()}

        get_resp = await former_member_client.get(f"/lists/{shared_list['id']}")
        assert get_resp.status_code == 403

        add_resp = await former_member_client.post(
            f"/lists/{shared_list['id']}/items",
            json={"name": "Pasta", "quantity": 1},
        )
        assert add_resp.status_code == 403

        select_resp = await former_member_client.post(
            "/lists/select",
            json={"list_id": shared_list["id"]},
        )
        assert select_resp.status_code == 403

    app.dependency_overrides.clear()
