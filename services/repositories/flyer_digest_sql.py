"""SQL for summaries; only existing jobs and notification JSON are used."""
EVENTS = """
SELECT * FROM public.notification_jobs
WHERE kind = 'flyer_published_recipient' AND payload ? 'flyer_id'
  AND NOT payload ? 'flyers' AND status IN ('pending', 'failed')
  AND available_at <= %s AND attempts < max_attempts
ORDER BY created_at, id
"""
ELIGIBLE = """
SELECT f.id::text AS flyer_id, f.supermarket_id::text, f.supermarket_name
FROM public.flyers f WHERE f.id = ANY(%s::uuid[])
  AND f.status = 'done' AND f.is_public
  AND (f.valid_from IS NULL OR f.valid_from <= %s)
  AND (f.valid_to IS NULL OR f.valid_to >= %s)
  AND EXISTS (SELECT 1 FROM public.offers o WHERE o.flyer_id = f.id
    AND o.is_confirmed AND o.offer_kind = 'published_target' AND (o.valid_from IS NULL OR o.valid_from <= %s)
    AND (o.valid_to IS NULL OR o.valid_to >= %s))
ORDER BY f.id
"""
TODAY = """
SELECT 1 FROM public.notification_jobs
WHERE idempotency_key = %s OR (
  kind = 'flyer_published_recipient' AND payload->>'user_id' = %s
  AND status = 'done' AND NOT payload ? 'consumed_by'
  AND NOT payload ? 'flyers' AND completed_at >= %s AND completed_at < %s
) LIMIT 1
"""
INSERT_DIGEST = """
INSERT INTO public.notification_jobs(kind, idempotency_key, payload, available_at)
VALUES ('flyer_published_recipient', %s, %s, %s)
ON CONFLICT (idempotency_key) DO NOTHING
"""
CONSUME = """
UPDATE public.notification_jobs SET status = 'done', completed_at = %s,
  updated_at = %s, payload = payload || jsonb_build_object('consumed_by', %s)
WHERE id = ANY(%s::uuid[])
"""
DEFER = """
UPDATE public.notification_jobs SET available_at = %s, updated_at = %s
WHERE id = ANY(%s::uuid[])
"""
ROLLOVER = """
SELECT id, payload FROM public.notification_jobs
WHERE kind = 'flyer_published_recipient' AND payload ? 'flyers'
  AND NOT payload ? 'delivery_started_at' AND payload->>'digest_date' < %s
  AND status IN ('pending', 'failed')
"""
RESTORE = """
UPDATE public.notification_jobs SET status = 'pending', available_at = %s,
  payload = payload - 'consumed_by', updated_at = %s
WHERE id = ANY(%s::uuid[])
"""
RESERVE = """
UPDATE public.notification_jobs SET payload = payload || %s::jsonb, updated_at = %s
WHERE id = %s
"""
INSERT_INBOX = """
INSERT INTO public.app_notifications(user_id, kind, title, body, data)
SELECT %s, 'flyer_published', %s, %s, %s
WHERE NOT EXISTS (SELECT 1 FROM public.app_notifications
  WHERE user_id = %s AND kind = 'flyer_published'
    AND data->>'aggregation_key' = %s)
"""
