-- Preserve AI localization metadata internally so interrupted crop uploads can resume.
ALTER TABLE public.offers
  ADD COLUMN IF NOT EXISTS packshot_source_page INTEGER,
  ADD COLUMN IF NOT EXISTS packshot_bbox JSONB;

CREATE INDEX IF NOT EXISTS idx_offers_pending_packshots
  ON public.offers (flyer_id)
  WHERE image_url IS NULL AND packshot_source_page IS NOT NULL AND packshot_bbox IS NOT NULL;
