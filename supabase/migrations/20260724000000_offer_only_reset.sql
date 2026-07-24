-- Intentional one-time reset for the offer-only model.
-- Storage bytes are deleted first by scripts/reset_offer_only_storage.py.

UPDATE public.shopping_lists
SET items = COALESCE((
  SELECT jsonb_agg(
    (item - 'pinned_product_id' - 'found_deals') || jsonb_build_object(
      'source', 'manual', 'pinned_offer_id', NULL
    )
  )
  FROM jsonb_array_elements(items) AS item
), '[]'::jsonb), updated_at = now();

DELETE FROM public.notification_jobs
WHERE kind = 'favorite_offers_published';
DELETE FROM public.app_notifications
WHERE kind = 'favorite_offer';
DO $$
BEGIN
  IF to_regclass('public.favorites') IS NOT NULL THEN
    DELETE FROM public.favorites;
  END IF;
END;
$$;
DROP TABLE IF EXISTS public.favorites;
DELETE FROM public.offers;
DELETE FROM public.flyer_targets;
DELETE FROM public.extraction_log;
DELETE FROM public.flyers;

DROP POLICY IF EXISTS products_anon_read ON public.products;
DROP POLICY IF EXISTS products_auth_read ON public.products;
DROP FUNCTION IF EXISTS public.search_products_catalog(text, integer);
DROP TABLE IF EXISTS public.products CASCADE;

ALTER TABLE public.offers
  DROP CONSTRAINT IF EXISTS offers_confirmed_product_required,
  DROP COLUMN IF EXISTS product_id,
  DROP COLUMN IF EXISTS draft_name,
  DROP COLUMN IF EXISTS draft_brand,
  DROP COLUMN IF EXISTS draft_category,
  DROP COLUMN IF EXISTS draft_subcategory,
  DROP COLUMN IF EXISTS draft_product_key,
  DROP COLUMN IF EXISTS draft_image_url;

ALTER TABLE public.offers
  ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS brand TEXT,
  ADD COLUMN IF NOT EXISTS category TEXT,
  ADD COLUMN IF NOT EXISTS subcategory TEXT,
  ADD COLUMN IF NOT EXISTS image_url TEXT,
  ADD COLUMN IF NOT EXISTS offer_key TEXT NOT NULL DEFAULT '';

DROP INDEX IF EXISTS public.idx_offers_product_id;
DROP INDEX IF EXISTS public.idx_offers_product_store;
DROP INDEX IF EXISTS public.idx_offers_flyer_draft_product_format;
CREATE UNIQUE INDEX IF NOT EXISTS idx_offers_flyer_offer_format
  ON public.offers(flyer_id, offer_key, format_key);
CREATE INDEX IF NOT EXISTS idx_offers_name ON public.offers(name);

CREATE OR REPLACE FUNCTION public.merge_shopping_list_items(
  base_items jsonb,
  incoming_items jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  item jsonb;
  existing jsonb;
  merged jsonb := COALESCE(base_items, '[]'::jsonb);
  match_index integer;
BEGIN
  FOR item IN
    SELECT value FROM jsonb_array_elements(COALESCE(incoming_items, '[]'::jsonb))
  LOOP
    SELECT value, ordinality - 1
    INTO existing, match_index
    FROM jsonb_array_elements(merged) WITH ORDINALITY
    WHERE (
      item->>'pinned_offer_id' IS NOT NULL
      AND value->>'pinned_offer_id' = item->>'pinned_offer_id'
    ) OR (
      item->>'pinned_offer_id' IS NULL
      AND lower(COALESCE(value->>'name', '')) = lower(COALESCE(item->>'name', ''))
      AND lower(COALESCE(value->>'brand', '')) = lower(COALESCE(item->>'brand', ''))
    )
    LIMIT 1;

    IF match_index IS NULL THEN
      merged := merged || jsonb_build_array(item);
    ELSE
      merged := jsonb_set(
        merged,
        ARRAY[match_index::text],
        jsonb_strip_nulls(existing || item)
      );
    END IF;
  END LOOP;
  RETURN merged;
END;
$$;
