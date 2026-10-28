"""Atomic planning and at-most-once push reservation without schema changes."""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from typing import Iterator

from psycopg2.extras import Json

from core.database import get_postgres_cursor, has_direct_postgres
from domain.flyer_digest import DigestConfigurationError, FlyerDigest, ROME, digest_opens
from services.repositories import flyer_digest_sql as sql

DIGEST_LOCK = "girospesa:daily-flyer-digests"


@contextmanager
def atomic(cursor) -> Iterator[None]:
    cursor.execute("BEGIN")
    committed = False
    try:
        yield
        committed = True
    finally:
        cursor.execute("COMMIT" if committed else "ROLLBACK")


class FlyerDigestRepository:
    @contextmanager
    def cycle(self):
        if not has_direct_postgres():
            raise DigestConfigurationError("Flyer digests require DB_DSN; no unsafe delivery fallback")
        with get_postgres_cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired", (DIGEST_LOCK,))
            acquired = cursor.fetchone()["acquired"]
            try:
                yield cursor if acquired else None
            finally:
                if acquired:
                    cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (DIGEST_LOCK,))

    def prepare(self, cursor, now: datetime) -> None:
        if now < digest_opens(now):
            return
        with atomic(cursor):
            self._rollover(cursor, now)
            cursor.execute(sql.EVENTS, (now,))
            grouped: dict[str, list[dict]] = defaultdict(list)
            for job in cursor.fetchall():
                grouped[job["payload"]["user_id"]].append(dict(job))
            for user_id, events in grouped.items():
                self._prepare_user(cursor, user_id, events, now)

    def _prepare_user(self, cursor, user_id: str, events: list[dict], now: datetime) -> None:
        day = now.astimezone(ROME).date()
        digest = FlyerDigest(user_id, day.isoformat(), ())
        midnight = datetime.combine(day, time(), ROME)
        cursor.execute(sql.TODAY, (digest.key, user_id, midnight, midnight + timedelta(days=1)))
        event_ids = [str(event["id"]) for event in events]
        if cursor.fetchone():
            cursor.execute(sql.DEFER, (digest_opens(now, tomorrow=True), now, event_ids))
            return
        items = self.eligible(cursor, [event["payload"]["flyer_id"] for event in events], now)
        if items:
            self._insert(cursor, FlyerDigest(user_id, day.isoformat(), tuple(items)), event_ids, now)
        cursor.execute(sql.CONSUME, (now, now, digest.key, event_ids))

    def _insert(self, cursor, digest: FlyerDigest, events: list[str], now: datetime) -> None:
        payload = {"user_id": digest.user_id, "digest_date": digest.day,
                   "flyers": list(digest.flyers), "event_job_ids": events}
        cursor.execute(sql.INSERT_DIGEST, (digest.key, Json(payload), now))

    def eligible(self, cursor, flyer_ids: list[str], now: datetime) -> list[dict]:
        if not flyer_ids:
            return []
        day = now.astimezone(ROME).date()
        cursor.execute(sql.ELIGIBLE, (flyer_ids, day, day, day, day))
        return [{**dict(row), "supermarket_name": row["supermarket_name"] or "Supermercato"}
                for row in cursor.fetchall()]

    def _rollover(self, cursor, now: datetime) -> None:
        cursor.execute(sql.ROLLOVER, (now.astimezone(ROME).date().isoformat(),))
        for job in cursor.fetchall():
            cursor.execute(sql.RESTORE, (digest_opens(now), now, job["payload"]["event_job_ids"]))
            cursor.execute("UPDATE public.notification_jobs SET status='dead', updated_at=%s, "
                           "last_error='Superseded by next daily digest' WHERE id=%s", (now, job["id"]))

    def reserve_delivery(self, cursor, job: dict, now: datetime) -> dict | None:
        with atomic(cursor):
            cursor.execute("SELECT payload FROM public.notification_jobs WHERE id=%s FOR UPDATE", (job["id"],))
            payload = cursor.fetchone()["payload"]
            if "delivery_started_at" in payload:
                return None
            cursor.execute("SELECT notifications_enabled, role FROM public.user_profiles WHERE id=%s",
                           (payload["user_id"],))
            profile = cursor.fetchone()
            items = self.eligible(cursor, [f["flyer_id"] for f in payload["flyers"]], now)
            if not profile or not items:
                return None
            digest = FlyerDigest(payload["user_id"], payload["digest_date"], tuple(items))
            return self._persist_delivery(cursor, job, digest, profile, now)

    def _persist_delivery(self, cursor, job: dict, digest: FlyerDigest, profile: dict, now: datetime) -> dict:
        title = ("Nuovi volantini nella tua zona" if profile["role"] == "customer"
                 else "Nuovi volantini disponibili")
        body, data = digest.body(), digest.data()
        cursor.execute(sql.INSERT_INBOX, (digest.user_id, title, body, Json(data), digest.user_id, digest.key))
        # Reserve before touching a provider: ambiguous network outcomes must not create noise.
        cursor.execute(sql.RESERVE, (Json({"delivery_started_at": now.isoformat()}), now, job["id"]))
        return {"user_id": digest.user_id, "title": title, "body": body, "data": data,
                "enabled": profile["notifications_enabled"]}
