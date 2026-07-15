from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from api.routers.lists import router as lists_router
from core.auth import get_current_user_id
from tests.conftest import wait_for_user_bootstrap

app = FastAPI()
app.include_router(lists_router, prefix="/lists")


@pytest.fixture()
def current_user():
    return {"id": None}


@pytest.fixture(autouse=True)
def _override_auth(current_user):
    app.dependency_overrides[get_current_user_id] = lambda: current_user["id"]
    yield
    app.dependency_overrides.clear()


def _create_user(supabase_client, prefix: str) -> dict:
    email = f"{prefix}_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id = resp.user.id
    wait_for_user_bootstrap(user_id)
    return {"id": user_id, "email": email}


@pytest.fixture()
def users(supabase_client, clean_db):
    owner = _create_user(supabase_client, "invite_owner")
    member = _create_user(supabase_client, "invite_member")
    try:
        yield {"owner": owner, "member": member}
    finally:
        supabase_client.auth.admin.delete_user(owner["id"])
        supabase_client.auth.admin.delete_user(member["id"])


@pytest.fixture()
def owner_list(supabase_client, users):
    row = (
        supabase_client.table("shopping_lists")
        .insert(
            {
                "user_id": users["owner"]["id"],
                "name": "Lista weekend",
                "items": [],
            }
        )
        .execute()
    ).data[0]
    (
        supabase_client.table("list_members")
        .insert(
            {
                "list_id": row["id"],
                "user_id": users["owner"]["id"],
                "role": "owner",
            }
        )
        .execute()
    )
    return row


class TestInviteFlowIntegration:
    async def test_owner_invites_member_and_member_accepts(
        self,
        supabase_client,
        users,
        owner_list,
        current_user,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            current_user["id"] = users["owner"]["id"]
            create_resp = await client.post(
                f"/lists/{owner_list['id']}/invites",
                json={"email": users["member"]["email"]},
            )

            current_user["id"] = users["member"]["id"]
            pending_resp = await client.get("/lists/invites/pending")
            accept_resp = await client.post(
                f"/lists/invites/{create_resp.json()['id']}/accept"
            )
            list_resp = await client.get(f"/lists/{owner_list['id']}")

        assert create_resp.status_code == 201
        invite = create_resp.json()
        assert invite["status"] == "pending"
        assert invite["invited_user_id"] == users["member"]["id"]
        assert invite["notification"]["kind"] == "list_invite"

        assert pending_resp.status_code == 200
        assert [row["id"] for row in pending_resp.json()] == [invite["id"]]

        assert accept_resp.status_code == 200
        assert accept_resp.json() == {"list_id": owner_list["id"]}
        assert list_resp.status_code == 200
        assert list_resp.json()["member_role"] == "member"

        invite_row = (
            supabase_client.table("list_invites")
            .select("status, accepted_by")
            .eq("id", invite["id"])
            .single()
            .execute()
        ).data
        assert invite_row == {
            "status": "accepted",
            "accepted_by": users["member"]["id"],
        }

        member_rows = (
            supabase_client.table("list_members")
            .select("role")
            .eq("list_id", owner_list["id"])
            .eq("user_id", users["member"]["id"])
            .execute()
        ).data
        assert member_rows == [{"role": "member"}]

        notification = (
            supabase_client.table("app_notifications")
            .select("read_at")
            .eq("id", invite["notification"]["id"])
            .single()
            .execute()
        ).data
        assert notification["read_at"] is not None
