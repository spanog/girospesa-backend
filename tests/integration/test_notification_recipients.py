from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg2
import pytest

from services.notification_jobs import NotificationJobWorker, enqueue_flyer_published


def _dsn() -> str:
    return os.environ["DB_DSN"]


@pytest.fixture()
def notification_municipality_context():
    supermarket_id = str(uuid.uuid4())
    user_ids = {name: str(uuid.uuid4()) for name in (
        "same_municipality", "nearby_municipality", "far", "missing", "manager", "admin",
    )}
    conn = psycopg2.connect(_dsn())
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            _insert_supermarket(cur, supermarket_id)
            _insert_users(cur, user_ids)
            _configure_profiles(cur, supermarket_id, user_ids)
        conn.commit()
        yield conn, supermarket_id, user_ids
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.notification_jobs WHERE payload->>'supermarket_id' = %s",
                (supermarket_id,),
            )
            cur.execute("DELETE FROM public.flyers WHERE supermarket_id = %s", (supermarket_id,))
            cur.execute("DELETE FROM auth.users WHERE id = ANY(%s::uuid[])", (list(user_ids.values()),))
            cur.execute("DELETE FROM public.supermarkets WHERE id = %s", (supermarket_id,))
        conn.commit()
        conn.close()


def test_flyer_notification_recipients_include_staff_and_nearby_customers(notification_municipality_context):
    conn, supermarket_id, user_ids = notification_municipality_context
    recipients = _recipient_ids(conn, supermarket_id)

    assert {
        user_ids["same_municipality"], user_ids["nearby_municipality"], user_ids["manager"], user_ids["admin"],
    } <= recipients
    assert user_ids["far"] not in recipients


def test_flyer_notification_jobs_persist_inbox_without_push(
    notification_municipality_context,
    supabase_client,
):
    conn, supermarket_id, _ = notification_municipality_context
    recipients = _recipient_ids(conn, supermarket_id)
    supabase_client.table("user_profiles").update(
        {"notifications_enabled": False}
    ).in_("id", list(recipients)).execute()
    flyer_id = str(uuid.uuid4())
    _insert_public_flyer(supabase_client, flyer_id, supermarket_id)
    enqueue_flyer_published(
        supabase_client,
        flyer_id=flyer_id,
        supermarket_id=supermarket_id,
        supermarket_name="Supermercato UAT",
        products_count=4,
    )
    result = NotificationJobWorker(supabase_client).run_pending()
    inbox = (
        supabase_client.table("app_notifications")
        .select("user_id")
        .eq("kind", "flyer_published")
        .contains("data", {"aggregation_key": f"flyer-published:{flyer_id}"})
        .execute()
        .data
    )
    assert result == {
        "claimed": len(recipients) + 1,
        "processed": len(recipients) + 1,
        "failed": 0,
    }
    assert {row["user_id"] for row in inbox} == recipients


def test_notification_worker_recovers_expired_processing_job(supabase_client):
    flyer_id = str(uuid.uuid4())
    job_id = _insert_expired_recipient_job(supabase_client, flyer_id)
    exhausted_job_id = _insert_expired_recipient_job(
        supabase_client,
        str(uuid.uuid4()),
        attempts=1,
        max_attempts=1,
    )
    _insert_public_flyer(supabase_client, flyer_id)

    try:
        result = NotificationJobWorker(supabase_client).run_pending()
        row = (
            supabase_client.table("notification_jobs")
            .select("status")
            .eq("id", job_id)
            .single()
            .execute()
            .data
        )
        exhausted_row = (
            supabase_client.table("notification_jobs")
            .select("status")
            .eq("id", exhausted_job_id)
            .single()
            .execute()
            .data
        )

        assert result == {"claimed": 1, "processed": 1, "failed": 0}
        assert row["status"] == "done"
        assert exhausted_row["status"] == "dead"
    finally:
        supabase_client.table("notification_jobs").delete().in_(
            "id", [job_id, exhausted_job_id]
        ).execute()
        supabase_client.table("flyers").delete().eq("id", flyer_id).execute()


def _insert_expired_recipient_job(
    supabase_client,
    flyer_id: str,
    attempts: int = 0,
    max_attempts: int = 5,
) -> str:
    response = supabase_client.table("notification_jobs").insert(
        {
            "kind": "flyer_published_recipient",
            "idempotency_key": f"flyer-published:{flyer_id}:user-1",
            "payload": {
                "flyer_id": flyer_id,
                "supermarket_id": str(uuid.uuid4()),
                "supermarket_name": "Supermercato UAT",
                "products_count": 1,
                "user_id": str(uuid.uuid4()),
            },
            "status": "processing",
            "attempts": attempts,
            "max_attempts": max_attempts,
            "available_at": datetime.now(UTC).isoformat(),
            "locked_at": (datetime.now(UTC) - timedelta(minutes=11)).isoformat(),
        }
    ).execute()
    return str(response.data[0]["id"])


def _recipient_ids(conn, supermarket_id: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM public.flyer_notification_recipients(%s)", (supermarket_id,))
        return {str(row[0]) for row in cur.fetchall()}


def _insert_public_flyer(
    supabase_client,
    flyer_id: str,
    supermarket_id: str | None = None,
) -> None:
    supabase_client.table("flyers").insert(
        {
            "id": flyer_id,
            "supermarket_id": supermarket_id,
            "supermarket_name": "Supermercato UAT",
            "file_url": "uat/flyer.pdf",
            "file_type": "pdf",
            "file_name": "flyer.pdf",
            "status": "done",
            "is_public": True,
        }
    ).execute()


def _insert_supermarket(cur, supermarket_id: str) -> None:
    cur.execute(
        """INSERT INTO public.supermarkets (id, name, slug, municipality_code)
           VALUES (%s, 'Supermercato UAT', %s, '015146')""",
        (supermarket_id, f"uat-notifications-{supermarket_id[:8]}"),
    )


def _insert_users(cur, user_ids: dict[str, str]) -> None:
    for name, user_id in user_ids.items():
        cur.execute(
            """INSERT INTO auth.users (id, email, encrypted_password, email_confirmed_at,
                  created_at, updated_at, raw_app_meta_data, raw_user_meta_data, aud, role)
               VALUES (%s, %s, '', NOW(), NOW(), NOW(), '{}'::jsonb, '{}'::jsonb,
                  'authenticated', 'authenticated')""",
            (user_id, f"uat-notifications-{name}-{user_id[:8]}@test.local"),
        )


def _configure_profiles(cur, supermarket_id: str, user_ids: dict[str, str]) -> None:
    cur.execute(
        """UPDATE public.user_profiles
           SET municipality_code = '015146', max_distance_km = 2
           WHERE id = %s""",
        (user_ids["same_municipality"],),
    )
    cur.execute(
        """UPDATE public.user_profiles
           SET municipality_code = '015205', max_distance_km = 10
           WHERE id = %s""",
        (user_ids["nearby_municipality"],),
    )
    cur.execute(
        """UPDATE public.user_profiles
           SET municipality_code = '058091', max_distance_km = 2
           WHERE id = %s""",
        (user_ids["far"],),
    )
    cur.execute(
        """UPDATE public.user_profiles SET role = 'supermarket_manager',
               managed_supermarket_id = %s
           WHERE id = %s""",
        (supermarket_id, user_ids["manager"]),
    )
    cur.execute(
        """UPDATE public.user_profiles SET role = 'admin'
           WHERE id = %s""",
        (user_ids["admin"],),
    )
