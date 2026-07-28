-- Query coverage for public offer discovery and flyer ingestion.
-- These predicates match the FastAPI public endpoints exactly.

CREATE INDEX IF NOT EXISTS idx_offers_public_name_trgm
  ON public.offers USING gin (name gin_trgm_ops)
  WHERE is_confirmed = true
    AND offer_kind = 'published_target';

CREATE INDEX IF NOT EXISTS idx_offers_public_supermarket_name
  ON public.offers (supermarket_id, name)
  WHERE is_confirmed = true
    AND offer_kind = 'published_target';

CREATE INDEX IF NOT EXISTS idx_flyers_public_feed
  ON public.flyers (created_at DESC)
  WHERE flyer_kind = 'published_target'
    AND status = 'done'
    AND is_public = true;

CREATE INDEX IF NOT EXISTS idx_flyers_file_hash
  ON public.flyers (file_hash)
  WHERE file_hash IS NOT NULL;
