"""Integration tests — POST /flyers/upload.

Verifies that the endpoint correctly inserts a `flyers` row with status='pending'
in the real local Supabase DB started via `supabase start`.

Storage is mocked because the endpoint test only needs DB-side effects; it does
not depend on direct local Storage container exposure.

Run:
    supabase start                # from girospesa-backend/
    pytest tests/integration/test_flyer_upload.py -v
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from tests.conftest import wait_for_user_bootstrap

from api.routers.flyers import router as flyers_router
from core.auth import get_current_user, get_current_user_id, require_admin_or_manager

# Minimal test app — only the flyers router.
# Avoids importing main.py which pulls in routes with FastAPI version-specific
# behaviours (e.g. 204 response-body assertion changed between 0.109 and 0.115).
app = FastAPI()
app.include_router(flyers_router, prefix="/flyers")

def _unique_pdf() -> bytes:
    """Minimal PDF bytes with a random suffix to guarantee a unique SHA-256 hash."""
    return b"%PDF-1.4 test-content " + uuid.uuid4().bytes


def _make_supabase_real_db_mock_storage() -> object:
    """Real Supabase client (local test DB) with storage layer mocked.

    The storage bucket is not reachable via the PostgREST port used in tests,
    so we stub out only the two storage calls the router makes.
    """
    import os

    from supabase import create_client

    real_client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )
    storage_mock = MagicMock()
    storage_mock.from_.return_value.upload.return_value = MagicMock()
    storage_mock.from_.return_value.get_public_url.return_value = (
        "https://storage.test/flyers/test.pdf"
    )

    class _ClientWithMockStorage:
        def __init__(self, client, storage):
            self._client = client
            self.storage = storage

        def __getattr__(self, name):
            return getattr(self._client, name)

    return _ClientWithMockStorage(real_client, storage_mock)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFlyerUploadIntegration:
    """Integration tests for POST /flyers/upload.

    Auth dependencies are overridden; storage is mocked; DB operations are real.
    """

    @pytest.fixture(autouse=True)
    def _override_auth(self, supabase_client):
        """Set auth dependency overrides for every test in this class."""
        email = f"admin_{uuid.uuid4().hex[:8]}@test.local"
        resp = supabase_client.auth.admin.create_user(
            {"email": email, "password": "Test_password_123!", "email_confirm": True}
        )
        user_id = resp.user.id
        wait_for_user_bootstrap(user_id)
        (
            supabase_client.table("user_profiles")
            .update({"role": "admin", "managed_supermarket_id": None})
            .eq("id", user_id)
            .execute()
        )

        admin_payload = {"sub": user_id, "app_metadata": {"role": "admin"}}

        app.dependency_overrides[get_current_user_id] = lambda: user_id
        app.dependency_overrides[get_current_user] = lambda: admin_payload
        app.dependency_overrides[require_admin_or_manager] = lambda: {
            "id": user_id,
            "role": "admin",
            "managed_supermarket_id": None,
        }
        yield
        app.dependency_overrides.clear()
        supabase_client.auth.admin.delete_user(user_id)

    @pytest.fixture()
    def supermarket(self, supabase_client, clean_db):
        return (
            supabase_client.table("supermarkets")
            .insert(
                {
                    "name": f"Upload Market {uuid.uuid4().hex[:6]}",
                    "slug": f"upload-market-{uuid.uuid4().hex[:8]}",
                    "lat": 45.0,
                    "lng": 9.0,
                }
            )
            .execute()
        ).data[0]

    async def test_pdf_upload_creates_pending_row(self, supabase_client, clean_db, supermarket):
        """Uploading a PDF creates a flyers row with status='pending' and file_type='pdf'."""
        sb = _make_supabase_real_db_mock_storage()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("api.routers.flyers.get_supabase", return_value=sb):
                resp = await client.post(
                    "/flyers/upload",
                    files={"file": ("volantino.pdf", io.BytesIO(_unique_pdf()), "application/pdf")},
                    data={"supermarket_ids": supermarket["id"]},
                )

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["supermarket_name"] == supermarket["name"]
        assert body["file_type"] == "pdf"
        assert body["user_id"] == app.dependency_overrides[get_current_user_id]()

        # Verify the row was actually persisted in the DB
        rows = (
            supabase_client.table("flyers")
            .select("id, status, supermarket_name, user_id, file_type")
            .eq("id", body["id"])
            .execute()
        )
        assert len(rows.data) == 1
        db_row = rows.data[0]
        assert db_row["status"] == "pending"
        assert db_row["supermarket_name"] == supermarket["name"]
        assert db_row["user_id"] == app.dependency_overrides[get_current_user_id]()
        assert db_row["file_type"] == "pdf"

    async def test_image_upload_creates_pending_row(self, supabase_client, clean_db, supermarket):
        """Uploading a JPEG image creates a row with file_type='image'."""
        sb = _make_supabase_real_db_mock_storage()
        jpeg_bytes = b"\xff\xd8\xff" + uuid.uuid4().bytes  # unique JPEG header

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("api.routers.flyers.get_supabase", return_value=sb):
                resp = await client.post(
                    "/flyers/upload",
                    files={"file": ("volantino.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
                    data={"supermarket_ids": supermarket["id"]},
                )

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["file_type"] == "image"

        rows = (
            supabase_client.table("flyers")
            .select("status, file_type")
            .eq("id", body["id"])
            .execute()
        )
        assert rows.data[0]["status"] == "pending"
        assert rows.data[0]["file_type"] == "image"

    async def test_upload_without_supermarket_name_uses_target_name(self, supabase_client, clean_db, supermarket):
        """Omitting supermarket_name is valid when supermarket_ids are provided."""
        sb = _make_supabase_real_db_mock_storage()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("api.routers.flyers.get_supabase", return_value=sb):
                resp = await client.post(
                    "/flyers/upload",
                    files={"file": ("v.pdf", io.BytesIO(_unique_pdf()), "application/pdf")},
                    data={"supermarket_ids": supermarket["id"]},
                )

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["supermarket_name"] == supermarket["name"]

        rows = (
            supabase_client.table("flyers")
            .select("supermarket_name, supermarket_id")
            .eq("id", body["id"])
            .execute()
        )
        assert rows.data[0]["supermarket_name"] == supermarket["name"]
        assert rows.data[0]["supermarket_id"] == supermarket["id"]

    async def test_duplicate_hash_and_supermarket_returns_409(self, supabase_client, clean_db, supermarket):
        """Uploading the same file+supermarket combination twice returns 409 Conflict."""
        pdf_content = _unique_pdf()
        sb1 = _make_supabase_real_db_mock_storage()
        sb2 = _make_supabase_real_db_mock_storage()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # First upload — must succeed
            with patch("api.routers.flyers.get_supabase", return_value=sb1):
                r1 = await client.post(
                    "/flyers/upload",
                    files={"file": ("v.pdf", io.BytesIO(pdf_content), "application/pdf")},
                    data={"supermarket_ids": supermarket["id"]},
                )
            assert r1.status_code == 201

            # Second upload — same bytes + same supermarket → duplicate → 409
            with patch("api.routers.flyers.get_supabase", return_value=sb2):
                r2 = await client.post(
                    "/flyers/upload",
                    files={"file": ("v.pdf", io.BytesIO(pdf_content), "application/pdf")},
                    data={"supermarket_ids": supermarket["id"]},
                )

        assert r2.status_code == 409
        assert "already exists" in r2.json()["detail"]

    async def test_upload_always_creates_private_flyer(self, supabase_client, clean_db, supermarket):
        """Upload ignores any is_public field; flyers stay private until offer confirmation."""
        sb = _make_supabase_real_db_mock_storage()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("api.routers.flyers.get_supabase", return_value=sb):
                resp = await client.post(
                    "/flyers/upload",
                    files={"file": ("v.pdf", io.BytesIO(_unique_pdf()), "application/pdf")},
                    data={"is_public": "true", "supermarket_ids": supermarket["id"]},
                )

        assert resp.status_code == 201
        body = resp.json()
        assert body["is_public"] is False

        rows = (
            supabase_client.table("flyers")
            .select("is_public")
            .eq("id", body["id"])
            .execute()
        )
        assert rows.data[0]["is_public"] is False
