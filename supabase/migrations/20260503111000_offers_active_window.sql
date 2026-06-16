CREATE OR REPLACE FUNCTION public.offer_is_currently_active(
  p_valid_from DATE,
  p_valid_to DATE
)
RETURNS BOOLEAN AS $$
  SELECT
    (p_valid_from IS NULL OR p_valid_from <= CURRENT_DATE)
    AND
    (p_valid_to IS NULL OR p_valid_to >= CURRENT_DATE);
$$ LANGUAGE sql STABLE;


CREATE OR REPLACE FUNCTION public.offers_compute_fields()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.price_original IS NOT NULL AND NEW.price_original > 0 THEN
    NEW.discount_pct := ROUND(
      ((NEW.price_original - NEW.price_offer) / NEW.price_original) * 100
    );
  ELSE
    NEW.discount_pct := NULL;
  END IF;

  NEW.is_active := public.offer_is_currently_active(NEW.valid_from, NEW.valid_to);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;


UPDATE public.offers
SET
  valid_from = valid_from,
  valid_to = valid_to;


DROP POLICY IF EXISTS "offers_anon_read" ON public.offers;
CREATE POLICY "offers_anon_read"
  ON public.offers FOR SELECT TO anon
  USING (
    public.offer_is_currently_active(valid_from, valid_to)
    AND is_confirmed = true
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
        AND public.offer_is_currently_active(o.valid_from, o.valid_to)
    )
  );
