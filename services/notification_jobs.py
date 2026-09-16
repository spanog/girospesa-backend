"""Durable, two-stage jobs for public flyer notifications."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from core.config import settings
from core.database import get_postgres_cursor, get_supabase, has_direct_postgres
from core.supabase_client import create_supabase_client
from services.push_notify import FcmAccessTokenProvider, deliver_public_flyer_published_to_recipient

logger = logging.getLogger(__name__)

JOB_FLYER_PUBLISHED = "flyer_published"
JOB_FLYER_PUBLISHED_RECIPIENT = "flyer_published_recipient"
STATUS_DEAD = "dead"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
RETRY_DELAY_MINUTES = 5
_ROME_TZ = ZoneInfo("Europe/Rome")
_FLYER_NOTIFICATION_TIME = time(hour=10)


def enqueue_flyer_published(
    sb: object,
    *,
    flyer_id: str,
    supermarket_id: str,
    supermarket_name: str,
    products_count: int,
    valid_from: str | None = None,
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
        available_at=_flyer_notification_available_at(valid_from),
    )


def _enqueue_recipient(sb: object, payload: dict[str, Any], user_id: str) -> None:
    _enqueue(
        sb,
        kind=JOB_FLYER_PUBLISHED_RECIPIENT,
        idempotency_key=f"flyer-published:{payload['flyer_id']}:{user_id}",
        payload={**payload, "user_id": user_id},
        available_at=datetime.now(UTC),
    )


def _supermarket_notification_location(sb: object, supermarket_id: str) -> str | None:
    response = sb.table("supermarkets").select(  # type: ignore[union-attr]
        "municipalities(name,province_code)"
    ).eq("id", supermarket_id).maybe_single().execute()
    supermarket = response.data if response else None
    if not isinstance(supermarket, dict):
        return None
    municipality = supermarket.get("municipalities") or {}
    name = str(municipality.get("name") or "").strip()
    province = str(municipality.get("province_code") or "").strip()
    return f"{name} ({province})" if name and province else name or None


def _enqueue(
    sb: object,
    *,
    kind: str,
    idempotency_key: str,
    payload: dict[str, Any],
    available_at: datetime,
) -> None:
    sb.table("notification_jobs").upsert(  # type: ignore[union-attr]
        {
            "kind": kind,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "status": STATUS_PENDING,
            "available_at": available_at.astimezone(UTC).isoformat(),
            "updated_at": _now_iso(),
        },
        on_conflict="idempotency_key",
        ignore_duplicates=True,
    ).execute()


def reschedule_flyer_published(
    sb: object,
    *,
    flyer_id: str,
    valid_from: str | None,
) -> None:
    update = {
        "available_at": _flyer_notification_available_at(valid_from).isoformat(),
        "updated_at": _now_iso(),
    }
    sb.table("notification_jobs").update(  # type: ignore[union-attr]
        update
    ).eq("kind", JOB_FLYER_PUBLISHED).eq(
        "idempotency_key", f"flyer-published:{flyer_id}"
    ).in_("status", [STATUS_PENDING, STATUS_FAILED]).execute()


def _flyer_notification_available_at(
    valid_from: str | None,
    now: datetime | None = None,
) -> datetime:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if valid_from is None:
        return current
    start = date.fromisoformat(valid_from)
    scheduled = datetime.combine(
        start, _FLYER_NOTIFICATION_TIME, _ROME_TZ
    ).astimezone(UTC)
    return max(current, scheduled)


class NotificationJobWorker:
    def __init__(
        self,
        sb: object | None = None,
        client_factory: Callable[[], object] | None = None,
        fcm_token_provider: FcmAccessTokenProvider | None = None,
    ) -> None:
        self._sb = sb
        self._client_factory = client_factory or _new_service_client
        self._fcm_token_provider = fcm_token_provider or FcmAccessTokenProvider()

    def run_pending(self, limit: int | None = None) -> dict[str, int]:
        batch_limit = limit or settings.notification_delivery_batch_size
        self._release_stale_jobs()
        parents = self._claim_jobs(JOB_FLYER_PUBLISHED, batch_limit)
        parent_results = [self._run_job(job, self._supabase()) for job in parents]
        recipients = self._claim_jobs(JOB_FLYER_PUBLISHED_RECIPIENT, batch_limit)
        recipient_results = self._run_recipients(recipients)
        results = parent_results + recipient_results
        return {
            "claimed": len(results),
            "processed": sum(results),
            "failed": len(results) - sum(results),
        }

    def _run_recipients(self, jobs: list[dict[str, Any]]) -> list[bool]:
        if not jobs:
            return []
        workers = min(settings.notification_delivery_workers, len(jobs))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(self._run_recipient_job, jobs))

    def _run_recipient_job(self, job: dict[str, Any]) -> bool:
        return self._run_job(job, self._client_factory())

    def _run_job(self, job: dict[str, Any], sb: object) -> bool:
        try:
            self._dispatch(job, sb)
        except Exception as exc:
            logger.exception("Notification job failed: %s", job.get("id"))
            self._mark_failed(sb, job, exc)
            return False
        self._mark_done(sb, str(job["id"]))
        return True

    def _dispatch(self, job: dict[str, Any], sb: object) -> None:
        payload = job.get("payload") or {}
        if not _flyer_is_notification_eligible(sb, str(payload.get("flyer_id", ""))):
            return
        if job.get("kind") == JOB_FLYER_PUBLISHED:
            self._materialize_recipients(sb, payload)
            return
        if job.get("kind") == JOB_FLYER_PUBLISHED_RECIPIENT:
            deliver_public_flyer_published_to_recipient(
                sb,
                **payload,
                fcm_token_provider=self._fcm_token_provider,
            )
            return
        raise ValueError(f"Unsupported notification job kind: {job.get('kind')}")

    def _materialize_recipients(self, sb: object, payload: dict[str, Any]) -> None:
        recipient_payload = {
            **payload,
            "supermarket_location": _supermarket_notification_location(
                sb, str(payload["supermarket_id"])
            ),
        }
        response = sb.rpc(  # type: ignore[union-attr]
            "flyer_notification_recipients",
            {"target_supermarket_id": payload["supermarket_id"]},
        ).execute()
        for recipient in response.data or []:
            user_id = recipient.get("user_id")
            if user_id:
                _enqueue_recipient(sb, recipient_payload, str(user_id))

    def _claim_jobs(self, kind: str, limit: int) -> list[dict[str, Any]]:
        if has_direct_postgres():
            with get_postgres_cursor() as cursor:
                cursor.execute(_CLAIM_SQL, {"kind": kind, "limit": max(1, limit)})
                return [dict(row) for row in cursor.fetchall()]
        return self._claim_jobs_via_supabase(kind, limit)

    def _claim_jobs_via_supabase(self, kind: str, limit: int) -> list[dict[str, Any]]:
        response = (
            self._supabase()
            .table("notification_jobs")
            .select("*")
            .eq("kind", kind)
            .in_("status", [STATUS_PENDING, STATUS_FAILED])
            .lte("available_at", _now_iso())
            .order("created_at")
            .limit(max(1, limit))
            .execute()
        )
        jobs = [job for job in response.data or [] if _attempts_left(job)]
        for job in jobs:
            self._mark_processing(job)
        return jobs

    def _mark_processing(self, job: dict[str, Any]) -> None:
        attempts = int(job.get("attempts") or 0) + 1
        job["attempts"] = attempts
        self._supabase().table("notification_jobs").update(
            {
                "status": STATUS_PROCESSING,
                "attempts": attempts,
                "locked_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        ).eq("id", job["id"]).execute()

    def _mark_done(self, sb: object, job_id: str) -> None:
        sb.table("notification_jobs").update(  # type: ignore[union-attr]
            {
                "status": STATUS_DONE,
                "completed_at": _now_iso(),
                "updated_at": _now_iso(),
                "last_error": None,
            }
        ).eq("id", job_id).execute()

    def _mark_failed(self, sb: object, job: dict[str, Any], exc: Exception) -> None:
        attempts = int(job.get("attempts") or 0)
        max_attempts = int(job.get("max_attempts") or 5)
        sb.table("notification_jobs").update(  # type: ignore[union-attr]
            {
                "status": STATUS_DEAD if attempts >= max_attempts else STATUS_FAILED,
                "available_at": _retry_at_iso(),
                "last_error": str(exc)[:1000],
                "updated_at": _now_iso(),
            }
        ).eq("id", job["id"]).execute()

    def _release_stale_jobs(self) -> None:
        if has_direct_postgres():
            released = self._release_stale_jobs_postgres()
        else:
            released = self._release_stale_jobs_supabase()
        if released:
            logger.warning("Recovered %s notification jobs with expired worker locks", released)

    def _release_stale_jobs_postgres(self) -> int:
        with get_postgres_cursor() as cursor:
            cursor.execute(_RELEASE_STALE_SQL, _stale_lock_parameters())
            return len(cursor.fetchall())

    def _release_stale_jobs_supabase(self) -> int:
        response = (
            self._supabase()
            .table("notification_jobs")
            .select("id, attempts, max_attempts")
            .eq("status", STATUS_PROCESSING)
            .lte("locked_at", _stale_lock_cutoff_iso())
            .execute()
        )
        jobs = response.data or []
        for job in jobs:
            self._supabase().table("notification_jobs").update(
                _stale_lock_update(job)
            ).eq("id", job["id"]).eq("status", STATUS_PROCESSING).execute()
        return len(jobs)

    def _supabase(self) -> object:
        if self._sb is None:
            self._sb = get_supabase()
        return self._sb


_CLAIM_SQL = """
WITH next_jobs AS (
  SELECT id
  FROM public.notification_jobs
  WHERE kind = %(kind)s
    AND status IN ('pending', 'failed')
    AND available_at <= NOW()
    AND attempts < max_attempts
  ORDER BY created_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT %(limit)s
)
UPDATE public.notification_jobs AS job
SET status = 'processing', attempts = job.attempts + 1,
    locked_at = NOW(), updated_at = NOW()
FROM next_jobs
WHERE job.id = next_jobs.id
RETURNING job.*;
"""


_RELEASE_STALE_SQL = """
UPDATE public.notification_jobs
SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
    locked_at = NULL, available_at = NOW(), updated_at = NOW(),
    last_error = CASE
        WHEN attempts >= max_attempts THEN 'Notification worker lock expired after final attempt.'
        ELSE 'Notification worker lock expired; requeued.'
    END
WHERE status = 'processing'
  AND locked_at <= NOW() - (%(timeout_seconds)s * INTERVAL '1 second')
RETURNING id;
"""


def _new_service_client() -> object:
    return create_supabase_client(settings.supabase_url, settings.supabase_secret_key)


def _attempts_left(job: dict[str, Any]) -> bool:
    return int(job.get("attempts") or 0) < int(job.get("max_attempts") or 5)


def _stale_lock_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(
        seconds=settings.notification_job_lock_timeout_seconds
    )


def _stale_lock_cutoff_iso() -> str:
    return _stale_lock_cutoff().isoformat()


def _stale_lock_parameters() -> dict[str, int]:
    return {"timeout_seconds": settings.notification_job_lock_timeout_seconds}


def _stale_lock_update(job: dict[str, Any]) -> dict[str, str | None]:
    exhausted = not _attempts_left(job)
    return {
        "status": STATUS_DEAD if exhausted else STATUS_PENDING,
        "locked_at": None,
        "available_at": _now_iso(),
        "updated_at": _now_iso(),
        "last_error": (
            "Notification worker lock expired after final attempt."
            if exhausted
            else "Notification worker lock expired; requeued."
        ),
    }


def _flyer_is_notification_eligible(sb: object, flyer_id: str) -> bool:
    response = sb.table("flyers").select(  # type: ignore[union-attr]
        "status, is_public, valid_from, valid_to"
    ).eq("id", flyer_id).maybe_single().execute()
    flyer = response.data if response else None
    if not isinstance(flyer, dict):
        return False
    if flyer.get("status") != "done" or not flyer.get("is_public"):
        return False
    today = datetime.now(_ROME_TZ).date()
    valid_from = flyer.get("valid_from")
    valid_to = flyer.get("valid_to")
    return (not valid_from or date.fromisoformat(str(valid_from)) <= today) and (
        not valid_to or date.fromisoformat(str(valid_to)) >= today
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _retry_at_iso() -> str:
    return (datetime.now(UTC) + timedelta(minutes=RETRY_DELAY_MINUTES)).isoformat()
