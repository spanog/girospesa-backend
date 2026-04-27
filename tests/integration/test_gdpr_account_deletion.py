"""Integration test — GDPR account deletion cascade.

Verifies that deleting a user triggers ON DELETE CASCADE on:
- push_subscriptions
- favorites
- user_profiles
- shopping_lists (and list_members)

Requires `supabase start` (local Supabase stack).

Run:
    supabase start
    pytest tests/integration/test_gdpr_account_deletion.py -v
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gdpr_user(supabase_client):
    """Create a real auth user and yield its ID. Delete after test."""
    email = f"gdpr_test_{uuid.uuid4().hex[:8]}@test.local"
    resp = supabase_client.auth.admin.create_user(
        {"email": email, "password": "Test_password_123!", "email_confirm": True}
    )
    user_id: str = resp.user.id
    yield user_id
    # Cleanup: delete user if still exists (cascade handles related rows)
    try:
        supabase_client.auth.admin.delete_user(user_id)
    except Exception:
        pass  # already deleted by the test


@pytest.fixture()
def supermarket_row(supabase_client):
    slug = f"gdpr-sm-{uuid.uuid4().hex[:6]}"
    row = (
        supabase_client.table("supermarkets")
        .insert({"name": "GDPR Test Market", "slug": slug, "lat": 45.0, "lng": 9.0})
        .execute()
    ).data[0]
    yield row
    supabase_client.table("supermarkets").delete().eq("id", row["id"]).execute()


@pytest.fixture()
def product_row(supabase_client):
    row = (
        supabase_client.table("products")
        .insert(
            {
                "name": f"Prodotto GDPR {uuid.uuid4().hex[:6]}",
                "brand": "TestBrand",
                "format": "500g",
            }
        )
        .execute()
    ).data[0]
    yield row
    supabase_client.table("products").delete().eq("id", row["id"]).execute()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_push_subscriptions_cascade_on_user_delete(supabase_client, gdpr_user):
    """DELETE auth.users → push_subscriptions deleted via ON DELETE CASCADE."""
    user_id = gdpr_user

    # Seed a push subscription for this user
    sub = (
        supabase_client.table("push_subscriptions")
        .insert(
            {
                "user_id": user_id,
                "endpoint": f"https://push.example.com/{uuid.uuid4().hex}",
                "p256dh": "test_p256dh_key",
                "auth_key": "test_auth_key",
                "user_agent": "TestBrowser/1.0",
            }
        )
        .execute()
    ).data[0]
    sub_id = sub["id"]

    # Verify the subscription exists
    pre_delete = (
        supabase_client.table("push_subscriptions").select("id").eq("id", sub_id).execute()
    )
    assert len(pre_delete.data) == 1, "Push subscription should exist before user deletion"

    # Delete user — this triggers ON DELETE CASCADE on push_subscriptions
    supabase_client.auth.admin.delete_user(user_id)

    # The gdpr_user fixture will try to delete again but we already did — that's OK
    # Verify push_subscriptions row is gone
    post_delete = (
        supabase_client.table("push_subscriptions").select("id").eq("id", sub_id).execute()
    )
    assert len(post_delete.data) == 0, (
        "push_subscriptions row must be deleted by CASCADE when user is deleted"
    )


def test_favorites_cascade_on_user_delete(supabase_client, gdpr_user, product_row):
    """DELETE auth.users → favorites deleted via ON DELETE CASCADE."""
    user_id = gdpr_user

    fav = (
        supabase_client.table("favorites")
        .insert({"user_id": user_id, "product_id": product_row["id"]})
        .execute()
    ).data[0]
    fav_id = fav["id"]

    pre_delete = (
        supabase_client.table("favorites").select("id").eq("id", fav_id).execute()
    )
    assert len(pre_delete.data) == 1

    supabase_client.auth.admin.delete_user(user_id)

    post_delete = (
        supabase_client.table("favorites").select("id").eq("id", fav_id).execute()
    )
    assert len(post_delete.data) == 0, (
        "favorites row must be deleted by CASCADE when user is deleted"
    )


def test_user_profiles_cascade_on_user_delete(supabase_client, gdpr_user):
    """DELETE auth.users → user_profiles deleted via ON DELETE CASCADE.

    user_profiles is auto-created by trigger on signup; verify it's gone after delete.
    """
    user_id = gdpr_user

    # user_profiles row may be auto-created by trigger; upsert to ensure it exists
    supabase_client.table("user_profiles").upsert({"id": user_id}).execute()

    pre_delete = (
        supabase_client.table("user_profiles").select("id").eq("id", user_id).execute()
    )
    assert len(pre_delete.data) == 1

    supabase_client.auth.admin.delete_user(user_id)

    post_delete = (
        supabase_client.table("user_profiles").select("id").eq("id", user_id).execute()
    )
    assert len(post_delete.data) == 0, (
        "user_profiles row must be deleted by CASCADE when user is deleted"
    )


def test_shopping_lists_cascade_on_user_delete(supabase_client, gdpr_user):
    """DELETE auth.users → shopping_lists + list_members deleted via ON DELETE CASCADE."""
    user_id = gdpr_user

    # Create a shopping list owned by this user
    lst = (
        supabase_client.table("shopping_lists")
        .insert({"name": "Lista GDPR Test", "user_id": user_id, "items": []})
        .execute()
    ).data[0]
    list_id = lst["id"]

    # Add owner to list_members
    supabase_client.table("list_members").insert(
        {"list_id": list_id, "user_id": user_id, "role": "owner"}
    ).execute()

    supabase_client.auth.admin.delete_user(user_id)

    # Both list and member rows must be gone
    post_list = (
        supabase_client.table("shopping_lists").select("id").eq("id", list_id).execute()
    )
    assert len(post_list.data) == 0, (
        "shopping_lists row must be deleted by CASCADE when owner user is deleted"
    )

    post_member = (
        supabase_client.table("list_members").select("list_id").eq("list_id", list_id).execute()
    )
    assert len(post_member.data) == 0, (
        "list_members rows must be deleted by CASCADE when list is deleted"
    )
