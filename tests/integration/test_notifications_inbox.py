from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from api.routers.notifications import router as notifications_router
from core.auth import get_current_user_id
from services.repositories import notifications_repository
from tests.conftest import wait_for_user_bootstrap

app = FastAPI()
app.include_router(notifications_router, prefix="/notifications")


@pytest.fixture()
def owner_user(supabase_client, clean_db):
    email = f"notif_owner_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id = resp.user.id
    wait_for_user_bootstrap(user_id)
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


@pytest.fixture()
def other_user(supabase_client):
    email = f"notif_other_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id = resp.user.id
    wait_for_user_bootstrap(user_id)
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


def _insert_notification(supabase_client, user_id: str, title: str) -> dict:
    return (
        supabase_client.table("app_notifications")
        .insert(
            {
                "user_id": user_id,
                "kind": "favorite_offer",
                "title": title,
                "body": "Body",
                "data": {"kind": "favorite_offer", "url": "/offerte"},
            }
        )
        .execute()
    ).data[0]


class TestNotificationsInboxIntegration:
    @pytest.fixture(autouse=True)
    def _override_auth(self, owner_user, monkeypatch):
        app.dependency_overrides[get_current_user_id] = lambda: owner_user
        monkeypatch.setattr(notifications_repository, "has_direct_postgres", lambda: True)
        yield
        app.dependency_overrides.clear()

    async def test_inbox_read_and_delete_operations_are_scoped_to_current_user(
        self,
        supabase_client,
        owner_user,
        other_user,
    ):
        first = _insert_notification(supabase_client, owner_user, "Prima")
        second = _insert_notification(supabase_client, owner_user, "Seconda")
        other = _insert_notification(supabase_client, other_user, "Altra")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            list_resp = await client.get("/notifications")
            read_resp = await client.post(f"/notifications/{first['id']}/read")
            read_all_resp = await client.post("/notifications/read-all")
            delete_resp = await client.post(
                "/notifications/delete-many",
                json={"notification_ids": [first["id"], other["id"]]},
            )

        assert list_resp.status_code == 200
        assert {row["id"] for row in list_resp.json()} == {first["id"], second["id"]}

        assert read_resp.status_code == 200
        assert read_resp.json()["id"] == first["id"]
        assert read_resp.json()["read_at"] is not None

        assert read_all_resp.status_code == 204
        assert delete_resp.status_code == 200
        assert delete_resp.json() == {
            "deleted_ids": [first["id"]],
            "missing_ids": [other["id"]],
        }

        owner_rows = (
            supabase_client.table("app_notifications")
            .select("id, read_at")
            .eq("user_id", owner_user)
            .execute()
        ).data
        assert len(owner_rows) == 1
        assert owner_rows[0]["id"] == second["id"]
        assert owner_rows[0]["read_at"] is not None

        other_rows = (
            supabase_client.table("app_notifications")
            .select("id")
            .eq("user_id", other_user)
            .execute()
        ).data
        assert other_rows == [{"id": other["id"]}]
