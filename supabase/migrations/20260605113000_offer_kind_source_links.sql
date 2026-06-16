ALTER TABLE public.offers
  ADD COLUMN IF NOT EXISTS offer_kind TEXT NOT NULL DEFAULT 'source_master',
  ADD COLUMN IF NOT EXISTS source_offer_id UUID REFERENCES public.offers(id) ON DELETE CASCADE;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'offers_offer_kind_check'
  ) THEN
    ALTER TABLE public.offers
      ADD CONSTRAINT offers_offer_kind_check
      CHECK (offer_kind IN ('source_master', 'published_target'));
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_offers_offer_kind
  ON public.offers(offer_kind);

CREATE INDEX IF NOT EXISTS idx_offers_source_offer_id
  ON public.offers(source_offer_id)
  WHERE source_offer_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_offers_source_offer_target_unique
  ON public.offers(source_offer_id, supermarket_id)
  WHERE source_offer_id IS NOT NULL;

UPDATE public.offers o
SET offer_kind = CASE
  WHEN f.flyer_kind = 'published_target' THEN 'published_target'
  ELSE 'source_master'
END
FROM public.flyers f
WHERE f.id = o.flyer_id
  AND (
    o.offer_kind IS NULL
    OR o.offer_kind <> CASE
      WHEN f.flyer_kind = 'published_target' THEN 'published_target'
      ELSE 'source_master'
    END
  );

WITH published_rows AS (
  SELECT
    published.id AS published_offer_id,
    source.id AS source_offer_id
  FROM public.offers published
  JOIN public.flyers published_flyer
    ON published_flyer.id = published.flyer_id
   AND published_flyer.flyer_kind = 'published_target'
  LEFT JOIN LATERAL (
    SELECT source.id
    FROM public.offers source
    WHERE source.flyer_id = published_flyer.source_flyer_id
      AND source.offer_kind = 'source_master'
      AND source.product_id IS NOT DISTINCT FROM published.product_id
      AND source.draft_product_key IS NOT DISTINCT FROM published.draft_product_key
      AND source.format_key IS NOT DISTINCT FROM published.format_key
    ORDER BY source.created_at, source.id
    LIMIT 1
  ) source ON TRUE
)
UPDATE public.offers published
SET source_offer_id = published_rows.source_offer_id
FROM published_rows
WHERE published.id = published_rows.published_offer_id
  AND published.offer_kind = 'published_target'
  AND published_rows.source_offer_id IS NOT NULL
  AND published.source_offer_id IS DISTINCT FROM published_rows.source_offer_id;

DROP POLICY IF EXISTS "offers_anon_read" ON public.offers;
CREATE POLICY "offers_anon_read"
  ON public.offers FOR SELECT TO anon
  USING (
    public.offer_is_currently_active(valid_from, valid_to)
    AND is_confirmed = true
    AND offer_kind = 'published_target'
    AND EXISTS (
      SELECT 1
      FROM public.flyers f
      WHERE f.id = offers.flyer_id
        AND f.is_public = true
    )
  );

DROP POLICY IF EXISTS "offers_auth_read" ON public.offers;
CREATE POLICY "offers_auth_read"
  ON public.offers FOR SELECT TO authenticated
  USING (
    (
      public.offer_is_currently_active(valid_from, valid_to)
      AND is_confirmed = true
      AND offer_kind = 'published_target'
    )
    OR EXISTS (
      SELECT 1
      FROM public.flyers f
      WHERE f.id = offers.flyer_id
        AND (f.is_public = true OR f.user_id = auth.uid())
    )
  );

DROP POLICY IF EXISTS "products_anon_read" ON public.products;
CREATE POLICY "products_anon_read"
  ON public.products FOR SELECT TO anon
  USING (
    EXISTS (
      SELECT 1
      FROM public.offers o
      JOIN public.flyers f ON f.id = o.flyer_id
      WHERE o.product_id = products.id
        AND f.is_public = true
        AND o.is_confirmed = true
        AND o.offer_kind = 'published_target'
        AND public.offer_is_currently_active(o.valid_from, o.valid_to)
    )
  );
