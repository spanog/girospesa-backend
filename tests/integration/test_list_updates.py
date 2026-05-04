"""
Integration test for the update_list_item RPC function.

The function signature:
  update_list_item(p_list_id uuid, p_item_id text, p_patch jsonb)

It patches an existing item in shopping_lists.items[] where item->>'id' = p_item_id,
but only if the calling user (auth.uid()) is a list member.

We test by calling the DB function directly via psycopg2 with the correct role context.
"""

import pytest
import uuid
import json
import os

import psycopg2
import psycopg2.extras

DB_DSN = os.getenv("DB_DSN")

pytestmark = pytest.mark.skipif(
    DB_DSN is None,
    reason="DB_DSN required; run with integration test stack.",
)


@pytest.fixture
def db_setup():
    """
    Creates a test user, a shopping list with two items, and a list_member row.
    Returns (conn, owner_id, list_id, item1_id, item2_id).
    Cleans up all test data on teardown.
    """
    owner_id = str(uuid.uuid4())
    list_id = str(uuid.uuid4())
    item1_id = str(uuid.uuid4())
    item2_id = str(uuid.uuid4())

    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False
    cur = conn.cursor()

    # Insert test user into auth.users
    cur.execute(
        """
        INSERT INTO auth.users (id, email, encrypted_password, email_confirmed_at,
            created_at, updated_at, raw_app_meta_data, raw_user_meta_data, aud, role)
        VALUES (%s, %s, '', NOW(), NOW(), NOW(), '{}', '{}', 'authenticated', 'authenticated')
        """,
        (owner_id, f"test-{owner_id[:8]}@test.local"),
    )

    # Insert shopping list with two items pre-populated
    items = [
        {
            "id": item1_id, "name": "Item 1", "quantity": 1,
            "unit": None, "checked": False, "checked_by": None, "checked_at": None,
            "added_by": owner_id, "added_at": "2026-04-15T00:00:00Z",
            "source": "manual", "pinned_product_id": None, "pinned_offer_id": None,
            "found_deals": [],
        },
        {
            "id": item2_id, "name": "Item 2", "quantity": 1,
            "unit": None, "checked": False, "checked_by": None, "checked_at": None,
            "added_by": owner_id, "added_at": "2026-04-15T00:00:00Z",
            "source": "manual", "pinned_product_id": None, "pinned_offer_id": None,
            "found_deals": [],
        },
    ]
    cur.execute(
        """
        INSERT INTO public.shopping_lists (id, user_id, name, is_active, items)
        VALUES (%s, %s, 'Test list', true, %s::jsonb)
        """,
        (list_id, owner_id, json.dumps(items)),
    )

    # Add owner as list member
    cur.execute(
        """
        INSERT INTO public.list_members (id, list_id, user_id, role)
        VALUES (%s, %s, %s, 'owner')
        """,
        (str(uuid.uuid4()), list_id, owner_id),
    )

    conn.commit()

    yield conn, owner_id, list_id, item1_id, item2_id

    # Teardown using a fresh connection (can't change autocommit mid-transaction)
    conn.close()
    cleanup_conn = psycopg2.connect(DB_DSN)
    cleanup_conn.autocommit = True
    cleanup_cur = cleanup_conn.cursor()
    cleanup_cur.execute("DELETE FROM public.list_members WHERE list_id = %s", (list_id,))
    cleanup_cur.execute("DELETE FROM public.shopping_lists WHERE id = %s", (list_id,))
    cleanup_cur.execute("DELETE FROM auth.users WHERE id = %s", (owner_id,))
    cleanup_conn.close()


def test_update_list_item_concurrent_patches(db_setup):
    """
    Verifies that update_list_item correctly patches two separate items in the
    shopping_lists.items JSONB array, simulating concurrent client updates.
    """
    conn, owner_id, list_id, item1_id, item2_id = db_setup
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Simulate the auth context so auth.uid() returns owner_id inside the function
    cur.execute("SET LOCAL role = authenticated;")
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": owner_id, "role": "authenticated"}),),
    )

    # Patch item 1: update quantity and mark checked
    patch1 = {"quantity": 2, "checked": True, "checked_by": owner_id, "checked_at": "2026-04-15T10:00:00Z"}
    cur.execute(
        "SELECT update_list_item(%s::uuid, %s::text, %s::jsonb)",
        (list_id, item1_id, json.dumps(patch1)),
    )

    # Patch item 2: update quantity
    patch2 = {"quantity": 3}
    cur.execute(
        "SELECT update_list_item(%s::uuid, %s::text, %s::jsonb)",
        (list_id, item2_id, json.dumps(patch2)),
    )

    conn.commit()

    # Verify both patches are reflected
    cur.execute("SELECT items FROM public.shopping_lists WHERE id = %s", (list_id,))
    row = cur.fetchone()
    items = row["items"]

    item1 = next(i for i in items if i["id"] == item1_id)
    item2 = next(i for i in items if i["id"] == item2_id)

    assert item1["quantity"] == 2
    assert item1["checked"] is True
    assert item1["checked_by"] == owner_id

    assert item2["quantity"] == 3
    assert item2["checked"] is False  # unchanged
    assert len(items) == 2
