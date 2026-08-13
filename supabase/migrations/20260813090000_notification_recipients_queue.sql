ALTER TABLE public.notification_jobs
  DROP CONSTRAINT IF EXISTS notification_jobs_kind_check;

ALTER TABLE public.notification_jobs
  ADD CONSTRAINT notification_jobs_kind_check CHECK (
    kind IN ('flyer_published', 'flyer_published_recipient', 'favorite_offers_published')
  );

CREATE OR REPLACE FUNCTION public.flyer_notification_recipients(
  target_supermarket_id uuid
)
RETURNS TABLE(user_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, extensions
AS $$
  WITH target AS (
    SELECT location
    FROM public.supermarkets
    WHERE id = target_supermarket_id
      AND is_active = true
      AND location IS NOT NULL
  )
  SELECT profile.id
  FROM public.user_profiles AS profile
  CROSS JOIN target
  CROSS JOIN LATERAL (
    SELECT COALESCE(profile.search_location, profile.home_location) AS location
  ) AS recipient
  WHERE profile.role = 'customer'
    AND recipient.location IS NOT NULL
    AND ST_DWithin(
      target.location,
      recipient.location,
      COALESCE(profile.max_distance_km, 10)::double precision * 1000
    );
$$;

REVOKE ALL ON FUNCTION public.flyer_notification_recipients(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.flyer_notification_recipients(uuid) FROM anon;
REVOKE ALL ON FUNCTION public.flyer_notification_recipients(uuid) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.flyer_notification_recipients(uuid) TO service_role;
