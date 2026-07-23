"""Queued notification jobs for publication events."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from core.database import get_postgres_cursor, get_supabase, has_direct_postgres
from services.push_notify import notify_favorite_offer_published, notify_public_flyer_published

logger = logging.getLogger(__name__)

JOB_FLYER_PUBLISHED = "flyer_published"
JOB_FAVORITE_OFFERS_PUBLISHED = "favorite_offers_published"
STATUS_DEAD = "dead"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
DEFAULT_LIMIT = 50
RETRY_DELAY_MINUTES = 5


def enqueue_flyer_published(
    sb: object,
    *,
    flyer_id: str,
    supermarket_id: str,
    supermarket_name: str,
    products_count: int,
) -> None:
    _enqueue(
        sb,
        kind=JOB_FLYER_PUBLISHED,
        idempotency_key=f"flyer-published:{flyer_id}",
        payload={
            "flyer_id": flyer_id,
            "supermarket_id": supermarket_id,
            "supermarket_name": supermarket_name,
            "products_count": products_count,
        },
    )


def enqueue_favorite_offers_published(sb: object, *, flyer_id: str) -> None:
    _enqueue(
        sb,
        kind=JOB_FAVORITE_OFFERS_PUBLISHED,
        idempotency_key=f"favorite-offers-published:{flyer_id}",
        payload={"flyer_id": flyer_id},
    )


def _enqueue(
    sb: object,
    *,
    kind: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> None:
    row = {
        "kind": kind,
        "idempotency_key": idempotency_key,
        "payload": payload,
        "status": STATUS_PENDING,
        "available_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    sb.table("notification_jobs").upsert(  # type: ignore[union-attr]
        row,
        on_conflict="idempotency_key",
    ).execute()


class NotificationJobWorker:
    def __init__(self, sb: object | None = None) -> None:
        self._sb = sb

    def run_pending(self, limit: int = DEFAULT_LIMIT) -> dict[str, int]:
        jobs = self._claim_jobs(limit)
        processed = 0
        failed = 0
        for job in jobs:
            if self._run_job(job):
                processed += 1
            else:
                failed += 1
        return {"claimed": len(jobs), "processed": processed, "failed": failed}

    def _run_job(self, job: dict[str, Any]) -> bool:
        try:
            self._dispatch(job)
        except Exception as exc:
            logger.exception("Notification job failed: %s", job.get("id"))
            self._mark_failed(job, exc)
            return False
        self._mark_done(str(job["id"]))
        return True

    def _dispatch(self, job: dict[str, Any]) -> None:
        kind = job.get("kind")
        payload = job.get("payload") or {}
        if kind == JOB_FLYER_PUBLISHED:
            self._process_flyer_published(payload)
            return
        if kind == JOB_FAVORITE_OFFERS_PUBLISHED:
            self._process_favorite_offers_published(payload)
            return
        raise ValueError(f"Unsupported notification job kind: {kind}")

    def _process_flyer_published(self, payload: dict[str, Any]) -> None:
        notify_public_flyer_published(self._supabase(), **payload)

    def _process_favorite_offers_published(self, payload: dict[str, Any]) -> None:
        flyer_id = str(payload.get("flyer_id") or "")
        if not flyer_id:
            return
        for offer in self._published_offers(flyer_id):
            notify_favorite_offer_published(self._supabase(), offer)

    def _published_offers(self, flyer_id: str) -> list[dict[str, Any]]:
        response = (
            self._supabase()
            .table("offers")
            .select("*")
            .eq("flyer_id", flyer_id)
            .eq("is_confirmed", True)
            .execute()
        )
        return [_offer_payload(row) for row in response.data or []]

    def _claim_jobs(self, limit: int) -> list[dict[str, Any]]:
        if not has_direct_postgres():
            return self._claim_jobs_via_supabase(limit)
        with get_postgres_cursor() as cursor:
            cursor.execute(_CLAIM_SQL, {"limit": max(1, limit)})
            return [dict(row) for row in cursor.fetchall()]

    def _claim_jobs_via_supabase(self, limit: int) -> list[dict[str, Any]]:
        response = (
            self._supabase()
            .table("notification_jobs")
            .select("*")
            .in_("status", [STATUS_PENDING, STATUS_FAILED])
            .lte("available_at", _now_iso())
            .order("created_at")
            .limit(max(1, limit))
            .execute()
        )
        jobs = [
            job
            for job in response.data or []
            if _attempts_left(job)
        ]
        for job in jobs:
            self._mark_processing(job)
        return jobs

    def _mark_processing(self, job: dict[str, Any]) -> None:
        next_attempts = int(job.get("attempts") or 0) + 1
        job["attempts"] = next_attempts
        self._supabase().table("notification_jobs").update(
            {
                "status": STATUS_PROCESSING,
                "attempts": next_attempts,
                "locked_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        ).eq("id", job["id"]).execute()

    def _mark_done(self, job_id: str) -> None:
        self._supabase().table("notification_jobs").update(
            {
                "status": STATUS_DONE,
                "completed_at": _now_iso(),
                "updated_at": _now_iso(),
                "last_error": None,
            }
        ).eq("id", job_id).execute()

    def _mark_failed(self, job: dict[str, Any], exc: Exception) -> None:
        attempts = int(job.get("attempts") or 0)
        max_attempts = int(job.get("max_attempts") or 5)
        status = STATUS_DEAD if attempts >= max_attempts else STATUS_FAILED
        self._supabase().table("notification_jobs").update(
            {
                "status": status,
                "available_at": _retry_at_iso(),
                "last_error": str(exc)[:1000],
                "updated_at": _now_iso(),
            }
        ).eq("id", job["id"]).execute()

    def _supabase(self) -> object:
        if self._sb is None:
            self._sb = get_supabase()
        return self._sb


_CLAIM_SQL = """
WITH next_jobs AS (
  SELECT id
  FROM public.notification_jobs
  WHERE status IN ('pending', 'failed')
    AND available_at <= NOW()
    AND attempts < max_attempts
  ORDER BY created_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT %(limit)s
)
UPDATE public.notification_jobs AS job
SET status = 'processing',
    attempts = job.attempts + 1,
    locked_at = NOW(),
    updated_at = NOW()
FROM next_jobs
WHERE job.id = next_jobs.id
RETURNING job.*;
"""


def _offer_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "discounted_price": row.get("discounted_price") or row.get("price_offer"),
        "original_price": row.get("original_price") or row.get("price_original"),
    }


def _attempts_left(job: dict[str, Any]) -> bool:
    attempts = int(job.get("attempts") or 0)
    max_attempts = int(job.get("max_attempts") or 5)
    return attempts < max_attempts


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _retry_at_iso() -> str:
    return (datetime.now(UTC) + timedelta(minutes=RETRY_DELAY_MINUTES)).isoformat()
