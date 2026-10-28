from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

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
            cur.execute("DELETE FROM public.notification_jobs WHERE payload->>'user_id' = ANY(%s)", (list(user_ids.values()),))
            cur.execute("DELETE FROM public.offers WHERE supermarket_id=%s", (supermarket_id,))
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
    _make_due(supabase_client, flyer_id)
    result = NotificationJobWorker(supabase_client, clock=_delivery_clock).run_pending()
    inbox = (
        supabase_client.table("app_notifications")
        .select("user_id")
        .eq("kind", "flyer_published")
        .contains("data", {"flyer_ids": [flyer_id]})
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
        result = NotificationJobWorker(supabase_client, clock=_delivery_clock).run_pending()
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

        assert result == {"claimed": 0, "processed": 0, "failed": 0}
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
    if supermarket_id:
        supabase_client.table("offers").insert({
            "flyer_id": flyer_id, "supermarket_id": supermarket_id,
            "supermarket_name": "Supermercato UAT", "name": "Pasta",
            "category": "Dispensa", "price_offer": 1.99,
            "format": {"tipo": "confezione_singola"}, "format_key": "confezione_singola",
            "format_label": "Confezione singola", "is_confirmed": True,
            "offer_kind": "published_target", "offer_key": uuid.uuid4().hex,
        }).execute()


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


def _delivery_clock():
    return datetime.now(ZoneInfo("Europe/Rome")).replace(hour=23, minute=0, second=0, microsecond=0)


def _make_due(sb, flyer_id):
    sb.table("notification_jobs").update({
        "available_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }).eq("idempotency_key", f"flyer-published:{flyer_id}").execute()


def _publish_event(sb, market):
    flyer = str(uuid.uuid4())
    _insert_public_flyer(sb, flyer, market)
    enqueue_flyer_published(sb, flyer_id=flyer, supermarket_id=market,
                            supermarket_name="Supermercato UAT", products_count=4)
    _make_due(sb, flyer)
    return flyer


def _inbox(sb, user):
    return sb.table("app_notifications").select("*").eq("user_id", user).eq("kind", "flyer_published").execute().data


def test_daily_digest_survives_batches_deletion_restart_and_next_day(
    notification_municipality_context, supabase_client, request,
):
    from tests.snapshot_utils import assert_matches_json_snapshot
    _, market, users = notification_municipality_context
    sb, user = supabase_client, users["same_municipality"]
    for recipient in _recipient_ids(notification_municipality_context[0], market):
        sb.table("user_profiles").update({"notifications_enabled": False}).eq("id", recipient).execute()
    first, second = _publish_event(sb, market), _publish_event(sb, market)
    worker = NotificationJobWorker(sb, clock=_delivery_clock)
    for _ in range(len(users)):
        worker.run_pending(limit=1)
    rows = _inbox(sb, user)
    assert len(rows) == 1
    assert set(rows[0]["data"]["flyer_ids"]) == {first, second}
    assert rows[0]["body"] == "Novità da Supermercato UAT."
    payload = {"kind": rows[0]["kind"], "title": rows[0]["title"], "body": rows[0]["body"],
               "data": {**rows[0]["data"], "aggregation_key": "<daily-key>"}}
    assert_matches_json_snapshot(request, "flyer_daily_digest", payload)
    sb.table("app_notifications").delete().eq("id", rows[0]["id"]).execute()
    late = _publish_event(sb, market)
    for _ in range(len(users)):
        NotificationJobWorker(sb, clock=_delivery_clock).run_pending(limit=1)
    assert _inbox(sb, user) == []
    tomorrow = lambda: _delivery_clock() + timedelta(days=1)
    for _ in range(len(users)):
        NotificationJobWorker(sb, clock=tomorrow).run_pending(limit=1)
    next_rows = _inbox(sb, user)
    assert len(next_rows) == 1
    assert next_rows[0]["data"]["flyer_ids"] == [late]


def test_summary_planner_excludes_concurrent_cycles_and_rolls_back(
    notification_municipality_context, supabase_client,
):
    from services.repositories.flyer_digest_repository import FlyerDigestRepository
    _, market, users = notification_municipality_context
    _publish_event(supabase_client, market)
    repo = FlyerDigestRepository()
    worker = NotificationJobWorker(supabase_client, clock=_delivery_clock)
    with repo.cycle() as cursor:
        assert cursor is not None
        with repo.cycle() as competitor:
            assert competitor is None
        worker._drain_parents(1)
        with patch.object(repo, "_insert", side_effect=ValueError("interrupted transaction")):
            with pytest.raises(ValueError):
                repo.prepare(cursor, _delivery_clock())
        cursor.execute("SELECT count(*) AS n FROM public.notification_jobs WHERE payload ? 'flyers' AND payload->>'user_id'=%s", (users["same_municipality"],))
        assert cursor.fetchone()["n"] == 0
        repo.prepare(cursor, _delivery_clock())
        cursor.execute("SELECT count(*) AS n FROM public.notification_jobs WHERE payload ? 'flyers' AND payload->>'user_id'=%s", (users["same_municipality"],))
        assert cursor.fetchone()["n"] >= 1


def test_summary_skips_expired_withdrawn_and_empty_flyers(
    notification_municipality_context, supabase_client,
):
    _, market, users = notification_municipality_context
    sb = supabase_client
    flyers = [_publish_event(sb, market) for _ in range(3)]
    sb.table("flyers").update({"is_public": False}).eq("id", flyers[0]).execute()
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    sb.table("flyers").update({"valid_to": yesterday}).eq("id", flyers[1]).execute()
    sb.table("offers").delete().eq("flyer_id", flyers[2]).execute()
    NotificationJobWorker(sb, clock=_delivery_clock).run_pending()
    assert _inbox(sb, users["same_municipality"]) == []


def test_reserved_digest_recovery_cannot_repeat_push_or_deleted_inbox(
    notification_municipality_context, supabase_client,
):
    _, market, users = notification_municipality_context
    _publish_event(supabase_client, market)
    with patch("services.notification_jobs.deliver_flyer_digest", side_effect=RuntimeError("ambiguous push outcome")) as send:
        first = NotificationJobWorker(supabase_client, clock=_delivery_clock).run_pending()
        assert first["failed"] > 0
        calls = send.call_count
        inbox = _inbox(supabase_client, users["same_municipality"])
        assert len(inbox) == 1
        supabase_client.table("app_notifications").delete().eq("id", inbox[0]["id"]).execute()
        retry = NotificationJobWorker(supabase_client, clock=_delivery_clock).run_pending()
        assert retry["failed"] == 0
        assert send.call_count == calls
        assert _inbox(supabase_client, users["same_municipality"]) == []


def test_unattempted_summary_rolls_over_and_rechecks_withdrawn_flyers(
    notification_municipality_context, supabase_client,
):
    from services.repositories.flyer_digest_repository import FlyerDigestRepository
    _, market, users = notification_municipality_context
    first, second = _publish_event(supabase_client, market), _publish_event(supabase_client, market)
    repo = FlyerDigestRepository()
    worker = NotificationJobWorker(supabase_client, clock=_delivery_clock)
    with repo.cycle() as cursor:
        worker._drain_parents(1)
        repo.prepare(cursor, _delivery_clock())
        supabase_client.table("flyers").update({"is_public": False}).eq("id", first).execute()
        tomorrow = _delivery_clock() + timedelta(days=1)
        repo.prepare(cursor, tomorrow)
        cursor.execute("SELECT * FROM public.notification_jobs WHERE payload ? 'flyers' "
                       "AND payload->>'user_id'=%s ORDER BY created_at", (users["same_municipality"],))
        jobs = cursor.fetchall()
        assert [job["status"] for job in jobs] == ["dead", "pending"]
        assert [item["flyer_id"] for item in jobs[1]["payload"]["flyers"]] == [second]
        delivery = repo.reserve_delivery(cursor, jobs[1], tomorrow)
        assert delivery["data"]["flyer_ids"] == [second]
        assert repo.reserve_delivery(cursor, jobs[1], tomorrow) is None
