from __future__ import annotations

import uuid

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
