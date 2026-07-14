UPDATE public.user_profiles
SET max_distance_km = 20
WHERE max_distance_km > 20;

DO $$
DECLARE
  constraint_name text;
BEGIN
  SELECT conname
  INTO constraint_name
  FROM pg_constraint
  WHERE conrelid = 'public.user_profiles'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) LIKE '%max_distance_km%';

  IF constraint_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.user_profiles DROP CONSTRAINT %I', constraint_name);
  END IF;
END $$;

ALTER TABLE public.user_profiles
  ADD CONSTRAINT user_profiles_max_distance_km_check
  CHECK (max_distance_km BETWEEN 1 AND 20);
