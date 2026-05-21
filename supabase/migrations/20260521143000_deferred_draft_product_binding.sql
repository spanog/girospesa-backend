-- Draft offers can be reviewed before creating a new canonical product.
-- Existing-product bindings remain visible; unbound drafts create products only on confirmation.

ALTER TABLE public.offers
  ADD COLUMN draft_name TEXT,
  ADD COLUMN draft_brand TEXT,
  ADD COLUMN draft_category TEXT,
  ADD COLUMN draft_subcategory TEXT,
  ADD COLUMN draft_product_key TEXT;

UPDATE public.offers o
SET
  draft_name = p.name,
  draft_brand = p.brand,
  draft_category = p.category,
  draft_subcategory = p.subcategory,
  draft_product_key = lower(trim(coalesce(p.name, ''))) || '|' || lower(trim(coalesce(p.brand, '')))
FROM public.products p
WHERE o.product_id = p.id;

ALTER TABLE public.offers
  ALTER COLUMN product_id DROP NOT NULL;

ALTER TABLE public.offers
  ADD CONSTRAINT offers_confirmed_product_required
  CHECK (is_confirmed = false OR product_id IS NOT NULL);

DROP INDEX IF EXISTS public.idx_offers_product_flyer_format;

CREATE UNIQUE INDEX idx_offers_flyer_draft_product_format
  ON public.offers(flyer_id, draft_product_key, format_key);
