ALTER TABLE public.flyers
  ADD COLUMN IF NOT EXISTS source_id TEXT,
  ADD COLUMN IF NOT EXISTS source_title TEXT;

CREATE INDEX IF NOT EXISTS idx_flyers_editorial_identity
  ON public.flyers (source_id, source_title, valid_from, valid_to)
  WHERE source_id IS NOT NULL AND source_title IS NOT NULL;
