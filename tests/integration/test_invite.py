"""Integration tests — invite flow.

Tests the full invitation flow:
  POST /lists/{list_id}/invite → GET /invite/{token} → POST /invite/{token}/accept

Requires `supabase start` (local Supabase stack).

Run:
    supabase start
    pytest tests/integration/test_invite.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.invite import router as invite_router
from api.routers.lists import router as lists_router
from core.auth import get_current_user_id
from tests.conftest import wait_for_user_bootstrap
from tests.snapshot_utils import assert_matches_json_snapshot

app = FastAPI()
app.include_router(lists_router, prefix="/lists")
app.include_router(invite_router, prefix="/invite")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def owner_user(supabase_client):
    """Auth user that acts as list owner. Cleans up on teardown."""
    email = f"owner_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    wait_for_user_bootstrap(user_id)
    supabase_client.table("user_profiles").update(
        {"display_name": "Test Owner"}
    ).eq("id", user_id).execute()
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def member_user(supabase_client):
    """Auth user that acts as invitee. Cleans up on teardown."""
    email = f"member_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    wait_for_user_bootstrap(user_id)
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def owner_list(supabase_client, owner_user, clean_db):
    """Shopping list owned by owner_user, with owner row in list_members."""
    list_row = (
        supabase_client.table("shopping_lists")
        .insert({"user_id": owner_user, "name": "Lista Test Invito", "items": []})
        .execute()
    ).data[0]
    supabase_client.table("list_members").insert(
        {"list_id": list_row["id"], "user_id": owner_user, "role": "owner"}
    ).execute()
    return list_row


@pytest.fixture()
def pending_invite(supabase_client, owner_list, owner_user):
    """Pending invite token seeded directly in DB, linked to owner_list."""
    invite_row = (
        supabase_client.table("list_invites")
        .insert({"list_id": owner_list["id"], "invited_by": owner_user})
        .execute()
    ).data[0]
    return invite_row


@pytest.fixture()
def shared_member(supabase_client, owner_list, member_user, owner_user):
    supabase_client.table("list_members").insert(
        {
            "list_id": owner_list["id"],
            "user_id": member_user,
            "role": "member",
            "invited_by": owner_user,
        }
    ).execute()
    return member_user


# ---------------------------------------------------------------------------
# Tests — POST /lists/{list_id}/invite
# ---------------------------------------------------------------------------


class TestCreateInvite:
    async def test_owner_creates_invite_returns_token(
        self, supabase_client, owner_user, owner_list, request
    ):
        """Owner POSTs /invite → 200, token is 64-char hex, status='pending'."""
        app.dependency_overrides[get_current_user_id] = lambda: owner_user
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                    resp = await client.post(f"/lists/{owner_list['id']}/invite", json={})
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert len(body["token"]) == 64
        assert body["list_id"] == owner_list["id"]
        assert body["invited_by"] == owner_user
        assert_matches_json_snapshot(request, "invite_create_response", body)

        row = (
            supabase_client.table("list_invites")
            .select("status, invited_by")
            .eq("id", body["id"])
            .execute()
        )
        assert len(row.data) == 1
        assert row.data[0]["status"] == "pending"
        assert row.data[0]["invited_by"] == owner_user

    async def test_non_member_cannot_create_invite(
        self, supabase_client, owner_list
    ):
        """A user with no membership in the list gets 403."""
        stranger_id = str(uuid.uuid4())
        app.dependency_overrides[get_current_user_id] = lambda: stranger_id
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                    resp = await client.post(f"/lists/{owner_list['id']}/invite", json={})
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 403

    async def test_invite_with_email(self, supabase_client, owner_user, owner_list):
        """Owner can include an optional email address in the invite payload."""
        app.dependency_overrides[get_current_user_id] = lambda: owner_user
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                    resp = await client.post(
                        f"/lists/{owner_list['id']}/invite",
                        json={"email": "invitato@example.com"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["email"] == "invitato@example.com"

    async def test_non_owner_member_cannot_create_invite(
        self, supabase_client, owner_list, shared_member
    ):
        """Shared member gets owner-only 403 on legacy invite creation."""
        app.dependency_overrides[get_current_user_id] = lambda: shared_member
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                with patch("api.routers.lists.get_supabase", return_value=supabase_client):
                    resp = await client.post(f"/lists/{owner_list['id']}/invite", json={})
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Only the owner can perform this action"


# ---------------------------------------------------------------------------
# Tests — GET /invite/{token}
# ---------------------------------------------------------------------------


class TestGetInvite:
    async def test_valid_token_returns_list_name_and_inviter(
        self, supabase_client, pending_invite, owner_list, request
    ):
        """Public endpoint returns list name, inviter display_name, and expiry."""
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.invite.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/invite/{pending_invite['token']}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["list_name"] == owner_list["name"]
        assert body["invited_by"] == "Test Owner"
        assert "expires_at" in body
        assert_matches_json_snapshot(request, "invite_get_public_payload", body)

    async def test_invalid_token_returns_404(self, supabase_client, owner_list):
        """A token that does not exist in the DB returns 404."""
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.invite.get_supabase", return_value=supabase_client):
                resp = await client.get(f"/invite/{'a' * 64}")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — POST /invite/{token}/accept
# ---------------------------------------------------------------------------


class TestAcceptInvite:
    async def test_accept_adds_member_and_marks_invite_accepted(
        self, supabase_client, member_user, pending_invite, owner_list
    ):
        """Happy path: member accepts invite → list_members row created, invite marked accepted."""
        app.dependency_overrides[get_current_user_id] = lambda: member_user
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                with patch("api.routers.invite.get_supabase", return_value=supabase_client):
                    resp = await client.post(f"/invite/{pending_invite['token']}/accept")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["list_id"] == owner_list["id"]

        members = (
            supabase_client.table("list_members")
            .select("role")
            .eq("list_id", owner_list["id"])
            .eq("user_id", member_user)
            .execute()
        )
        assert len(members.data) == 1
        assert members.data[0]["role"] == "member"

        invite = (
            supabase_client.table("list_invites")
            .select("status, accepted_by")
            .eq("id", pending_invite["id"])
            .execute()
        )
        assert invite.data[0]["status"] == "accepted"
        assert invite.data[0]["accepted_by"] == member_user

    async def test_accept_is_idempotent_when_already_member(
        self, supabase_client, member_user, pending_invite, owner_list
    ):
        """Accepting when already a member does not create duplicate list_members rows."""
        supabase_client.table("list_members").insert(
            {"list_id": owner_list["id"], "user_id": member_user, "role": "member"}
        ).execute()

        app.dependency_overrides[get_current_user_id] = lambda: member_user
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                with patch("api.routers.invite.get_supabase", return_value=supabase_client):
                    resp = await client.post(f"/invite/{pending_invite['token']}/accept")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200

        members = (
            supabase_client.table("list_members")
            .select("id")
            .eq("list_id", owner_list["id"])
            .eq("user_id", member_user)
            .execute()
        )
        assert len(members.data) == 1  # no duplicate

    async def test_accept_invalid_token_returns_404(
        self, supabase_client, member_user, owner_list
    ):
        """Accepting a non-existent token returns 404."""
        app.dependency_overrides[get_current_user_id] = lambda: member_user
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                with patch("api.routers.invite.get_supabase", return_value=supabase_client):
                    resp = await client.post(f"/invite/{'a' * 64}/accept")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 404
