"""Performance tests — flyer upload endpoint with a large (40-page equivalent) PDF.

Verifies that POST /flyers/upload can handle a large file (≈ 10 MB, simulating
a 40-page volantino) within acceptable latency. Storage is mocked so this
measures only FastAPI processing: file read, SHA-256 computation, DB insert.

Does NOT require `supabase start` for the upload endpoint itself (DB insert
is fast), but the Supabase guard in the session conftest will still require it.

Run:
    supabase start
    pytest tests/performance/test_upload_performance.py -v -s
"""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from api.routers.flyers import router as flyers_router
from core.auth import get_current_user, get_current_user_id, require_admin_or_manager

app = FastAPI()
app.include_router(flyers_router, prefix="/flyers")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

UPLOAD_LIMIT_MS = 5_000   # wall time for a 10 MB upload (SHA-256 + DB insert)


def _make_large_pdf(size_mb: float = 10.0) -> bytes:
    """Generate a synthetic PDF blob of approximately `size_mb` megabytes.

    The endpoint validates content-type but does not parse PDF structure,
    so a padded header is sufficient for upload performance testing.
    """
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    target_bytes = int(size_mb * 1024 * 1024)
    # Deterministic repeated pattern — avoids randomness overhead
    chunk = b"0123456789abcdef" * 64  # 1 KB chunk
    needed = max(0, target_bytes - len(header))
    repetitions = needed // len(chunk) + 1
    padding = (chunk * repetitions)[:needed]
    return header + padding


def _make_supabase_real_db_mock_storage() -> object:
    """Create a fresh Supabase client with storage layer mocked.

    A fresh client is required because the session-scoped supabase_client
    fixture has `storage` as a read-only property that cannot be reassigned.
    """
    import os
    from supabase import create_client

    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    storage_mock = MagicMock()
    storage_mock.from_.return_value.upload.return_value = MagicMock()
    storage_mock.from_.return_value.get_public_url.return_value = (
        "https://storage.test/flyers/large.pdf"
    )
    sb._storage = storage_mock  # type: ignore[attr-defined]
    return sb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_upload_user(supabase_client):
    """Temporary auth user for upload performance tests."""
    import uuid
    email = f"perf_upload_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    yield user_id
    supabase_client.auth.admin.delete_user(user_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUploadPerformance:

    @pytest.fixture(autouse=True)
    def _override_auth(self, perf_upload_user):
        admin_payload = {"sub": perf_upload_user, "app_metadata": {"role": "admin"}}
        app.dependency_overrides[get_current_user_id] = lambda: perf_upload_user
        app.dependency_overrides[get_current_user] = lambda: admin_payload
        app.dependency_overrides[require_admin_or_manager] = lambda: {
            "id": perf_upload_user,
            "role": "admin",
            "managed_supermarket_id": None,
        }
        yield
        app.dependency_overrides.clear()

    async def test_large_pdf_upload_under_threshold(self, supabase_client, clean_db):
        """Uploading a ~10 MB PDF (40-page volantino equivalent) completes in < 5s.

        Measures: file read, SHA-256 hash, duplicate check query, DB insert.
        Storage upload is mocked (network latency excluded by design).
        """
        pdf_bytes = _make_large_pdf(size_mb=10.0)
        sb = _make_supabase_real_db_mock_storage()

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.flyers.get_supabase", return_value=sb):
                start = time.perf_counter()
                resp = await client.post(
                    "/flyers/upload",
                    files={"file": ("volantino_grande.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
                    data={"supermarket_name": "Esselunga"},
                )
                elapsed_ms = (time.perf_counter() - start) * 1000

        assert resp.status_code == 201, f"Unexpected status {resp.status_code}: {resp.text}"
        assert elapsed_ms < UPLOAD_LIMIT_MS, (
            f"10 MB PDF upload took {elapsed_ms:.0f}ms — exceeds {UPLOAD_LIMIT_MS}ms threshold. "
            "SHA-256 computation or DB insert may be the bottleneck."
        )

    async def test_duplicate_detection_is_fast(self, supabase_client, clean_db):
        """Duplicate hash detection for a 10 MB file adds negligible latency.

        The second upload of the same content should return 409 quickly —
        the duplicate check must not re-read the entire file from DB.
        """
        pdf_bytes = _make_large_pdf(size_mb=10.0)
        sb = _make_supabase_real_db_mock_storage()

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            with patch("api.routers.flyers.get_supabase", return_value=sb):
                # First upload — must succeed
                r1 = await client.post(
                    "/flyers/upload",
                    files={"file": ("v.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
                    data={"supermarket_name": "Coop"},
                )
                assert r1.status_code == 201

                # Second upload — duplicate detection (only hash comparison)
                sb2 = _make_supabase_real_db_mock_storage()
                with patch("api.routers.flyers.get_supabase", return_value=sb2):
                    start = time.perf_counter()
                    r2 = await client.post(
                        "/flyers/upload",
                        files={"file": ("v.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
                        data={"supermarket_name": "Coop"},
                    )
                    elapsed_ms = (time.perf_counter() - start) * 1000

        assert r2.status_code == 409
        assert elapsed_ms < UPLOAD_LIMIT_MS, (
            f"Duplicate detection took {elapsed_ms:.0f}ms — should be faster than first upload."
        )

    async def test_file_size_at_limit_is_rejected_quickly(self):
        """Files exceeding 50 MB are rejected before any DB access — should be < 500ms."""
        oversized = b"%PDF-1.4\n" + b"x" * (51 * 1024 * 1024)

        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            start = time.perf_counter()
            resp = await client.post(
                "/flyers/upload",
                files={"file": ("too_big.pdf", io.BytesIO(oversized), "application/pdf")},
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

        assert resp.status_code == 413
        assert elapsed_ms < 500, (
            f"Oversized file rejection took {elapsed_ms:.0f}ms — should fail fast before DB access."
        )
