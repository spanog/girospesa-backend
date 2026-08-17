CREATE OR REPLACE FUNCTION public.flyer_notification_recipients(
  target_supermarket_id uuid
)
RETURNS TABLE(user_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, extensions
AS $$
  SELECT profile.id
  FROM public.user_profiles AS profile
  WHERE profile.role = 'admin'

  UNION

  SELECT profile.id
  FROM public.user_profiles AS profile
  CROSS JOIN public.supermarkets AS target
  CROSS JOIN LATERAL (
    SELECT COALESCE(profile.search_location, profile.home_location) AS location
  ) AS recipient
  WHERE target.id = target_supermarket_id
    AND target.is_active = true
    AND target.location IS NOT NULL
    AND profile.role = 'customer'
    AND recipient.location IS NOT NULL
    AND ST_DWithin(
      target.location,
      recipient.location,
      COALESCE(profile.max_distance_km, 10)::double precision * 1000
    );
$$;
