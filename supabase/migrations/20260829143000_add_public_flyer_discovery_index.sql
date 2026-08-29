-- Public flyer discovery filters by nearby branches before ordering newest first.
CREATE INDEX IF NOT EXISTS idx_flyers_public_discovery_by_supermarket
  ON public.flyers (supermarket_id, created_at DESC)
  WHERE flyer_kind = 'published_target'
    AND status = 'done'
    AND is_public = true;
