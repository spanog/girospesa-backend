"""Integration test — manager flyer draft-review lifecycle."""

from __future__ import annotations

import io
import os
import sys
import time
import types
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from tests.conftest import wait_for_user_bootstrap

_config_mod = types.ModuleType("core.config")
_config_mod.settings = types.SimpleNamespace(
    supabase_url=os.environ.get("SUPABASE_URL", ""),
    supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
    supabase_jwt_secret=os.environ.get("SUPABASE_JWT_SECRET", ""),
    llm_provider="gemini",
    google_api_key="",
    gemini_model="gemma-4-31b-it",
    frontend_url="http://localhost:3000",
)
sys.modules["core.config"] = _config_mod

from api.routers.flyers import router as flyers_router
from api.routers.products import router as products_router
from core.auth import get_current_user_id, require_admin_or_manager

_FUTURE_DATE = "2099-12-31"


def _create_test_app(manager_profile: dict) -> FastAPI:
    app = FastAPI()
    app.include_router(flyers_router, prefix="/flyers")
    app.include_router(products_router, prefix="/products")

    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for dependency in dependant.dependencies:
            call = dependency.call
            if call is get_current_user_id or getattr(call, "__name__", "") == "get_current_user_id":
                app.dependency_overrides[call] = lambda: manager_profile["id"]
            if call is require_admin_or_manager or getattr(call, "__name__", "") == "require_admin_or_manager":
                app.dependency_overrides[call] = lambda: manager_profile
    return app


class _SupabaseWithMockStorage:
    def __init__(self, inner, storage):
        self._inner = inner
        self.storage = storage

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def _unique_pdf() -> bytes:
    return b"%PDF-1.4 lifecycle-test " + uuid.uuid4().bytes


def _make_supabase_real_db_mock_storage() -> object:
    from supabase import create_client

    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    storage_mock = MagicMock()

    def _storage_bucket(name: str) -> MagicMock:
        bucket = MagicMock()
        bucket.upload.return_value = MagicMock()
        bucket.get_public_url.side_effect = (
            lambda path: f"https://storage.test/{name}/{path}"
        )
        return bucket

    storage_mock.from_.side_effect = _storage_bucket
    return _SupabaseWithMockStorage(sb, storage_mock)


@pytest.fixture()
def supermarket(supabase_client, clean_db):
    return (
        supabase_client.table("supermarkets")
        .insert(
            {
                "name": "Lifecycle Market",
                "slug": f"lifecycle-{uuid.uuid4().hex[:8]}",
                "lat": 45.0,
                "lng": 9.0,
            }
        )
        .execute()
    ).data[0]


@pytest.fixture()
def manager_profile(supabase_client, supermarket):
    email = f"manager_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id = resp.user.id
    wait_for_user_bootstrap(user_id)
    (
        supabase_client.table("user_profiles")
        .update({"role": "supermarket_manager", "managed_supermarket_id": supermarket["id"]})
        .eq("id", user_id)
        .execute()
    )
    profile = {
        "id": user_id,
        "role": "supermarket_manager",
        "managed_supermarket_id": supermarket["id"],
    }
    yield profile
    supabase_client.auth.admin.delete_user(user_id)


def _make_mock_extraction_service(supabase_client):
    class MockExtractionService:
        def run(self, flyer_id: str) -> None:
            flyer = (
                supabase_client.table("flyers")
                .select("id, supermarket_id, supermarket_name, valid_from, valid_to")
                .eq("id", flyer_id)
                .single()
                .execute()
            ).data
            product_payload = {
                "name": "Pasta Barilla",
                "brand": "Barilla",
                "category": "dispensa",
            }
            try:
                product = (
                    supabase_client.table("products")
                    .insert(product_payload)
                    .execute()
                ).data[0]
            except Exception:
                product = (
                    supabase_client.table("products")
                    .select("id")
                    .eq("name", product_payload["name"])
                    .eq("brand", product_payload["brand"])
                    .single()
                    .execute()
                ).data
            (
                supabase_client.table("offers")
                .insert(
                    {
                        "product_id": product["id"],
                        "draft_name": "Pasta Barilla",
                        "draft_brand": "Barilla",
                        "draft_category": "dispensa",
                        "draft_subcategory": None,
                        "draft_product_key": "pasta barilla|barilla",
                        "flyer_id": flyer_id,
                        "supermarket_id": flyer["supermarket_id"],
                        "supermarket_name": flyer["supermarket_name"],
                        "price_offer": 1.99,
                        "price_original": 2.99,
                        "valid_from": flyer["valid_from"],
                        "valid_to": flyer["valid_to"],
                        "is_confirmed": False,
                    }
                )
                .execute()
            )
            (
                supabase_client.table("flyers")
                .update({"status": "done", "products_count": 1, "pages_count": 1})
                .eq("id", flyer_id)
                .execute()
            )

    return MockExtractionService


class TestOfferLifecycleIntegration:
    async def test_manager_offer_review_lifecycle(
        self,
        supabase_client,
        manager_profile,
        supermarket,
    ):
        sb = _make_supabase_real_db_mock_storage()
        mock_service = _make_mock_extraction_service(supabase_client)
        app = _create_test_app(manager_profile)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with (
                patch("api.routers.flyers.get_supabase", return_value=sb),
                patch("api.routers.products.get_supabase", return_value=supabase_client),
                patch("services.extraction.service.ExtractionService", mock_service),
            ):
                upload_resp = await client.post(
                    "/flyers/upload",
                    files={"file": ("lifecycle.pdf", io.BytesIO(_unique_pdf()), "application/pdf")},
                    data={
                        "supermarket_name": supermarket["name"],
                        "valid_from": "2099-01-01",
                        "valid_to": _FUTURE_DATE,
                        "is_public": "false",
                    },
                )

                assert upload_resp.status_code == 201
                flyer_id = upload_resp.json()["id"]
                assert upload_resp.json()["status"] == "pending"

                trigger_resp = await client.post(f"/flyers/{flyer_id}/extract")
                assert trigger_resp.status_code == 202
                assert trigger_resp.json() == {"status": "processing", "flyer_id": flyer_id}

                flyer_row = None
                deadline = time.time() + 2
                while time.time() < deadline:
                    rows = (
                        supabase_client.table("flyers")
                        .select("status, products_count")
                        .eq("id", flyer_id)
                        .execute()
                    ).data
                    if rows:
                        flyer_row = rows[0]
                        if flyer_row["status"] == "done":
                            break
                    time.sleep(0.05)

                assert flyer_row is not None
                assert flyer_row["status"] == "done"
                assert flyer_row["products_count"] == 1

                before_confirm_resp = await client.get("/products")
                assert before_confirm_resp.status_code == 200
                assert before_confirm_resp.json()["items"] == []

                drafts_resp = await client.get(f"/flyers/{flyer_id}/draft-offers")
                assert drafts_resp.status_code == 200
                drafts = drafts_resp.json()
                assert len(drafts) == 1
                assert drafts[0]["name"] == "Pasta Barilla"
                assert drafts[0]["is_confirmed"] is False
                offer_id = drafts[0]["id"]

                detach_resp = await client.patch(
                    f"/flyers/{flyer_id}/draft-offers/{offer_id}",
                    json={"detach_product": True},
                )
                assert detach_resp.status_code == 200
                assert detach_resp.json()["binding_status"] == "new_on_confirm"

                image_resp = await client.post(
                    f"/flyers/{flyer_id}/draft-offers/{offer_id}/image",
                    files={"file": ("pasta.png", b"png", "image/png")},
                )
                assert image_resp.status_code == 200
                assert "/product-images/" in image_resp.json()["image_url"]

                patch_resp = await client.patch(
                    f"/flyers/{flyer_id}/draft-offers/{offer_id}",
                    json={"price_offer": 2.49},
                )
                assert patch_resp.status_code == 200
                assert patch_resp.json()["price_offer"] == pytest.approx(2.49)

                confirm_resp = await client.post(f"/flyers/{flyer_id}/offers/confirm")
                assert confirm_resp.status_code == 200
                assert confirm_resp.json() == {"confirmed": 1, "flyer_id": flyer_id}

                confirmed_offer = (
                    supabase_client.table("offers")
                    .select("is_confirmed, price_offer")
                    .eq("id", offer_id)
                    .single()
                    .execute()
                ).data
                assert confirmed_offer["is_confirmed"] is True
                assert confirmed_offer["price_offer"] == pytest.approx(2.49)

                created_product = (
                    supabase_client.table("products")
                    .select("image_url")
                    .eq("name", "Pasta Barilla")
                    .eq("brand", "Barilla")
                    .single()
                    .execute()
                ).data
                assert created_product["image_url"] is not None
                assert "/product-images/" in created_product["image_url"]

                after_confirm_resp = await client.get("/products")
                assert after_confirm_resp.status_code == 200
                assert after_confirm_resp.json()["items"] == []
