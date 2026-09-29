ALTER TABLE public.flyers
  ADD COLUMN IF NOT EXISTS publication_key TEXT;

CREATE INDEX IF NOT EXISTS idx_flyers_publication_key
  ON public.flyers (publication_key)
  WHERE publication_key IS NOT NULL;
