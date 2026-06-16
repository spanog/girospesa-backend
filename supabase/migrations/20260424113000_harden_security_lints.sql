-- Harden Supabase security lints and align legacy extraction log schema.

ALTER TABLE IF EXISTS public.analytics_data ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename = 'scraping_log'
  ) AND NOT EXISTS (
    SELECT 1
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename = 'extraction_log'
  ) THEN
    ALTER TABLE public.scraping_log RENAME TO extraction_log;
  END IF;
END $$;

ALTER TABLE IF EXISTS public.extraction_log ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema = 'public'
      AND table_name = 'extraction_log'
      AND constraint_name = 'scraping_log_pkey'
  ) THEN
    ALTER TABLE public.extraction_log
      RENAME CONSTRAINT scraping_log_pkey TO extraction_log_pkey;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema = 'public'
      AND table_name = 'extraction_log'
      AND constraint_name = 'scraping_log_flyer_id_fkey'
  ) THEN
    ALTER TABLE public.extraction_log
      RENAME CONSTRAINT scraping_log_flyer_id_fkey TO extraction_log_flyer_id_fkey;
  END IF;
END $$;

ALTER INDEX IF EXISTS public.idx_scraping_log_flyer_id
  RENAME TO idx_extraction_log_flyer_id;

ALTER INDEX IF EXISTS public.idx_scraping_log_event_type
  RENAME TO idx_extraction_log_event_type;

ALTER INDEX IF EXISTS public.idx_scraping_log_created_at
  RENAME TO idx_extraction_log_created_at;

DROP POLICY IF EXISTS "Anyone can insert flyer requests" ON public.flyer_requests;

ALTER FUNCTION public.products_update_tsv() SET search_path = public;
ALTER FUNCTION public.create_list(text) SET search_path = public;
ALTER FUNCTION public.update_list_item(uuid, text, jsonb) SET search_path = public;
ALTER FUNCTION public.offers_compute_fields() SET search_path = public;
ALTER FUNCTION public.set_updated_at() SET search_path = public;
