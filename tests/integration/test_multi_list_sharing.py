from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.lists import router as lists_router
from api.routers.notifications import router as notifications_router
from core.auth import get_current_user_id

app = FastAPI()
app.include_router(lists_router, prefix="/lists")
app.include_router(notifications_router, prefix="/notifications")


@pytest.fixture()
def owner_user(supabase_client):
    email = f"owner_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
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
    supabase_client, owner_user, clean_db
):
    async with await _client_as(owner_user["id"]) as client:
        active_resp = await client.get("/lists/active")
        assert active_resp.status_code == 200
        default_list = active_resp.json()
        assert default_list["is_default"] is True

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

    invite_row = (
        supabase_client.table("list_invites")
        .select("status, accepted_by")
        .eq("id", invite["id"])
        .single()
        .execute()
        .data
    )
    assert invite_row["status"] == "accepted"
    assert invite_row["accepted_by"] == member_user["id"]

    member_rows = (
        supabase_client.table("list_members")
        .select("user_id")
        .eq("list_id", owner_list["id"])
        .eq("user_id", member_user["id"])
        .execute()
        .data
    )
    assert len(member_rows) == 1

    notification_rows = (
        supabase_client.table("app_notifications")
        .select("read_at")
        .eq("user_id", member_user["id"])
        .eq("kind", "list_invite")
        .execute()
        .data
    )
    assert notification_rows
    assert notification_rows[0]["read_at"] is not None

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

    invite_row = (
        supabase_client.table("list_invites")
        .select("status, declined_at")
        .eq("id", invite["id"])
        .single()
        .execute()
        .data
    )
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

    (
        supabase_client.table("push_subscriptions")
        .insert(
            {
                "user_id": member_user["id"],
                "endpoint": f"https://push.example.com/{uuid.uuid4().hex}",
                "p256dh": "test_p256dh_key",
                "auth_key": "test_auth_key",
                "user_agent": "TestBrowser/1.0",
            }
        )
        .execute()
    )

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

    profile_row = (
        supabase_client.table("user_profiles")
        .select("active_list_id")
        .eq("id", member_user["id"])
        .single()
        .execute()
        .data
    )
    member_default = (
        supabase_client.table("shopping_lists")
        .select("id")
        .eq("user_id", member_user["id"])
        .eq("is_default", True)
        .single()
        .execute()
        .data
    )
    assert profile_row["active_list_id"] == member_default["id"]

    notification_rows = (
        supabase_client.table("app_notifications")
        .select("kind, title, body, data")
        .eq("user_id", member_user["id"])
        .eq("kind", "list_deleted")
        .execute()
        .data
    )
    assert len(notification_rows) == 1
    notification = notification_rows[0]
    assert notification["title"] == "Lista rimossa"
    assert notification["data"]["list_id"] == shared_list["id"]
    assert notification["data"]["list_name"] == shared_list["name"]
    assert notification["data"]["deleted_by"] == "Owner Test"
    assert notification["data"]["url"] == "/lista"
    assert "Weekend" in notification["body"]

    owner_notifications = (
        supabase_client.table("app_notifications")
        .select("id")
        .eq("user_id", owner_user["id"])
        .eq("kind", "list_deleted")
        .execute()
        .data
    )
    assert owner_notifications == []

    owner_profile = (
        supabase_client.table("user_profiles")
        .select("active_list_id")
        .eq("id", owner_user["id"])
        .single()
        .execute()
        .data
    )
    assert owner_profile["active_list_id"] == default_list["id"]

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

    remaining_rows = (
        supabase_client.table("shopping_lists")
        .select("id")
        .eq("id", shared_list["id"])
        .execute()
        .data
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

    (
        supabase_client.table("push_subscriptions")
        .insert(
            {
                "user_id": member_user["id"],
                "endpoint": f"https://push.example.com/{uuid.uuid4().hex}",
                "p256dh": "test_p256dh_key",
                "auth_key": "test_auth_key",
                "user_agent": "TestBrowser/1.0",
            }
        )
        .execute()
    )

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

    profile_row = (
        supabase_client.table("user_profiles")
        .select("active_list_id")
        .eq("id", member_user["id"])
        .single()
        .execute()
        .data
    )
    member_default = (
        supabase_client.table("shopping_lists")
        .select("id")
        .eq("user_id", member_user["id"])
        .eq("is_default", True)
        .single()
        .execute()
        .data
    )
    assert profile_row["active_list_id"] == member_default["id"]

    notification_rows = (
        supabase_client.table("app_notifications")
        .select("kind, title, body, data")
        .eq("user_id", member_user["id"])
        .eq("kind", "list_member_removed")
        .execute()
        .data
    )
    assert len(notification_rows) == 1
    notification = notification_rows[0]
    assert notification["title"] == "Rimosso dalla lista"
    assert notification["data"]["list_id"] == shared_list["id"]
    assert notification["data"]["list_name"] == shared_list["name"]
    assert notification["data"]["removed_by"] == "Owner Test"
    assert notification["data"]["url"] == "/lista"
    assert "Weekend" in notification["body"]

    owner_notifications = (
        supabase_client.table("app_notifications")
        .select("id")
        .eq("user_id", owner_user["id"])
        .eq("kind", "list_member_removed")
        .execute()
        .data
    )
    assert owner_notifications == []

    mock_push.assert_called_once()
    push_kwargs = mock_push.call_args.kwargs
    assert push_kwargs["title"] == "Rimosso dalla lista"
    assert push_kwargs["body"] == "Owner Test ti ha rimosso dalla lista Weekend"
    assert push_kwargs["data"]["list_id"] == shared_list["id"]
    assert push_kwargs["data"]["removed_by"] == "Owner Test"
    assert push_kwargs["data"]["url"] == "/lista"

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

    (
        supabase_client.table("push_subscriptions")
        .insert(
            {
                "user_id": owner_user["id"],
                "endpoint": f"https://push.example.com/{uuid.uuid4().hex}",
                "p256dh": "test_p256dh_key",
                "auth_key": "test_auth_key",
                "user_agent": "TestBrowser/1.0",
            }
        )
        .execute()
    )

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

    profile_row = (
        supabase_client.table("user_profiles")
        .select("active_list_id")
        .eq("id", member_user["id"])
        .single()
        .execute()
        .data
    )
    member_default = (
        supabase_client.table("shopping_lists")
        .select("id")
        .eq("user_id", member_user["id"])
        .eq("is_default", True)
        .single()
        .execute()
        .data
    )
    assert profile_row["active_list_id"] == member_default["id"]

    owner_notifications = (
        supabase_client.table("app_notifications")
        .select("kind, title, body, data")
        .eq("user_id", owner_user["id"])
        .eq("kind", "list_member_left")
        .execute()
        .data
    )
    assert len(owner_notifications) == 1
    notification = owner_notifications[0]
    assert notification["title"] == "Membro uscito dalla lista"
    assert notification["data"]["list_id"] == shared_list["id"]
    assert notification["data"]["list_name"] == shared_list["name"]
    assert notification["data"]["left_by"] == "Member Test"
    assert notification["data"]["url"] == "/lista"
    assert "Weekend" in notification["body"]

    member_notifications = (
        supabase_client.table("app_notifications")
        .select("id")
        .eq("user_id", member_user["id"])
        .eq("kind", "list_member_left")
        .execute()
        .data
    )
    assert member_notifications == []

    mock_push.assert_called_once()
    push_kwargs = mock_push.call_args.kwargs
    assert push_kwargs["title"] == "Membro uscito dalla lista"
    assert push_kwargs["body"] == "Member Test ha lasciato la lista Weekend"
    assert push_kwargs["data"]["list_id"] == shared_list["id"]
    assert push_kwargs["data"]["left_by"] == "Member Test"
    assert push_kwargs["data"]["url"] == "/lista"

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
