from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.notification_jobs import (
    NotificationJobWorker,
    enqueue_favorite_offers_published,
    enqueue_flyer_published,
)


def test_enqueue_flyer_published_is_idempotent_by_flyer():
    sb = MagicMock()

    enqueue_flyer_published(
        sb,
        flyer_id="flyer-1",
        supermarket_id="sup-1",
        supermarket_name="Conad",
        products_count=12,
    )

    row = sb.table.return_value.upsert.call_args.args[0]
    assert row["kind"] == "flyer_published"
    assert row["idempotency_key"] == "flyer-published:flyer-1"
    assert row["payload"]["supermarket_name"] == "Conad"
    sb.table.return_value.upsert.assert_called_once_with(
        row,
        on_conflict="idempotency_key",
    )


def test_enqueue_favorite_offers_published_is_idempotent_by_flyer():
    sb = MagicMock()

    enqueue_favorite_offers_published(sb, flyer_id="flyer-1")

    row = sb.table.return_value.upsert.call_args.args[0]
    assert row["kind"] == "favorite_offers_published"
    assert row["idempotency_key"] == "favorite-offers-published:flyer-1"
    assert row["payload"] == {"flyer_id": "flyer-1"}


def test_worker_processes_favorite_offer_job_from_queue():
    sb = MagicMock()
    jobs_table = MagicMock()
    offers_table = MagicMock()
    job = _job("favorite_offers_published", {"flyer_id": "flyer-1"})
    offer = {"id": "offer-1", "flyer_id": "flyer-1", "price_offer": 1.99}
    jobs_table.select.return_value.in_.return_value.lte.return_value.order.return_value.limit.return_value.execute.return_value.data = [job]
    offers_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [offer]
    sb.table.side_effect = lambda name: jobs_table if name == "notification_jobs" else offers_table

    with (
        patch("services.notification_jobs.has_direct_postgres", return_value=False),
        patch("services.notification_jobs.notify_favorite_offer_published") as notify_mock,
    ):
        result = NotificationJobWorker(sb).run_pending(limit=1)

    assert result == {"claimed": 1, "processed": 1, "failed": 0}
    notify_mock.assert_called_once()
    notified_offer = notify_mock.call_args.args[1]
    assert notified_offer["discounted_price"] == 1.99
    jobs_table.update.assert_called()


def test_worker_processes_flyer_published_job_from_queue():
    sb = MagicMock()
    jobs_table = MagicMock()
    payload = {
        "flyer_id": "flyer-1",
        "supermarket_id": "sup-1",
        "supermarket_name": "Conad",
        "products_count": 3,
    }
    jobs_table.select.return_value.in_.return_value.lte.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        _job("flyer_published", payload)
    ]
    sb.table.return_value = jobs_table

    with (
        patch("services.notification_jobs.has_direct_postgres", return_value=False),
        patch("services.notification_jobs.notify_public_flyer_published") as notify_mock,
    ):
        result = NotificationJobWorker(sb).run_pending(limit=1)

    assert result == {"claimed": 1, "processed": 1, "failed": 0}
    notify_mock.assert_called_once_with(sb, **payload)


def test_worker_skips_jobs_without_attempts_left_in_supabase_fallback():
    sb = MagicMock()
    jobs_table = MagicMock()
    jobs_table.select.return_value.in_.return_value.lte.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        _job("flyer_published", {}, attempts=5, max_attempts=5)
    ]
    sb.table.return_value = jobs_table

    with patch("services.notification_jobs.has_direct_postgres", return_value=False):
        result = NotificationJobWorker(sb).run_pending(limit=1)

    assert result == {"claimed": 0, "processed": 0, "failed": 0}
    jobs_table.update.assert_not_called()


def _job(
    kind: str,
    payload: dict,
    *,
    attempts: int = 1,
    max_attempts: int = 5,
) -> dict:
    return {
        "id": "job-1",
        "kind": kind,
        "payload": payload,
        "attempts": attempts,
        "max_attempts": max_attempts,
    }
