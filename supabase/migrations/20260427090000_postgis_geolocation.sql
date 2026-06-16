-- Migration: postgis_geolocation
-- Adds indexed geography columns for distance queries.

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;

SET search_path = public, extensions;

ALTER TABLE public.supermarkets
  ADD COLUMN IF NOT EXISTS location extensions.geography(Point, 4326);

ALTER TABLE public.user_profiles
  ADD COLUMN IF NOT EXISTS home_location extensions.geography(Point, 4326),
  ADD COLUMN IF NOT EXISTS search_location extensions.geography(Point, 4326);

CREATE OR REPLACE FUNCTION public.set_supermarket_location_from_lat_lng()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
  IF NEW.lat IS NULL OR NEW.lng IS NULL THEN
    NEW.location := NULL;
  ELSE
    NEW.location := ST_SetSRID(ST_MakePoint(NEW.lng::double precision, NEW.lat::double precision), 4326)::geography;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_profile_locations_from_lat_lng()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
  IF NEW.home_lat IS NULL OR NEW.home_lng IS NULL THEN
    NEW.home_location := NULL;
  ELSE
    NEW.home_location := ST_SetSRID(ST_MakePoint(NEW.home_lng::double precision, NEW.home_lat::double precision), 4326)::geography;
  END IF;

  IF NEW.search_lat IS NULL OR NEW.search_lng IS NULL THEN
    NEW.search_location := NULL;
  ELSE
    NEW.search_location := ST_SetSRID(ST_MakePoint(NEW.search_lng::double precision, NEW.search_lat::double precision), 4326)::geography;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS supermarkets_set_location ON public.supermarkets;
CREATE TRIGGER supermarkets_set_location
  BEFORE INSERT OR UPDATE OF lat, lng ON public.supermarkets
  FOR EACH ROW EXECUTE FUNCTION public.set_supermarket_location_from_lat_lng();

DROP TRIGGER IF EXISTS user_profiles_set_locations ON public.user_profiles;
CREATE TRIGGER user_profiles_set_locations
  BEFORE INSERT OR UPDATE OF home_lat, home_lng, search_lat, search_lng ON public.user_profiles
  FOR EACH ROW EXECUTE FUNCTION public.set_profile_locations_from_lat_lng();

UPDATE public.supermarkets
SET location = ST_SetSRID(ST_MakePoint(lng::double precision, lat::double precision), 4326)::geography
WHERE lat IS NOT NULL
  AND lng IS NOT NULL;

UPDATE public.user_profiles
SET home_location = ST_SetSRID(ST_MakePoint(home_lng::double precision, home_lat::double precision), 4326)::geography
WHERE home_lat IS NOT NULL
  AND home_lng IS NOT NULL;

UPDATE public.user_profiles
SET search_location = ST_SetSRID(ST_MakePoint(search_lng::double precision, search_lat::double precision), 4326)::geography
WHERE search_lat IS NOT NULL
  AND search_lng IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_supermarkets_location
  ON public.supermarkets USING gist (location);

CREATE INDEX IF NOT EXISTS idx_user_profiles_home_location
  ON public.user_profiles USING gist (home_location);

CREATE INDEX IF NOT EXISTS idx_user_profiles_search_location
  ON public.user_profiles USING gist (search_location);

CREATE OR REPLACE FUNCTION public.nearby_supermarkets(
  user_lat double precision,
  user_lng double precision,
  radius_m double precision DEFAULT 10000
)
RETURNS TABLE(id uuid, distance_km double precision)
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
  WITH user_point AS (
    SELECT ST_SetSRID(ST_MakePoint(user_lng, user_lat), 4326)::geography AS location
  )
  SELECT
    sm.id,
    ST_Distance(sm.location, user_point.location) / 1000 AS distance_km
  FROM public.supermarkets AS sm
  CROSS JOIN user_point
  WHERE sm.is_active = true
    AND sm.location IS NOT NULL
    AND radius_m > 0
    AND ST_DWithin(sm.location, user_point.location, radius_m)
  ORDER BY distance_km ASC, sm.name ASC;
$$;

GRANT EXECUTE ON FUNCTION public.nearby_supermarkets(double precision, double precision, double precision)
  TO anon, authenticated, service_role;

RESET search_path;
