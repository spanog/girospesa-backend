DROP INDEX IF EXISTS public.idx_offers_product_flyer_format;

CREATE UNIQUE INDEX idx_offers_product_flyer_format
  ON public.offers(product_id, flyer_id, format_key);
