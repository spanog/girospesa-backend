DROP POLICY "offers_anon_read" ON public.offers;
DROP POLICY "offers_auth_read" ON public.offers;

-- anon: active + confirmed + public flyer
CREATE POLICY "offers_anon_read"
  ON public.offers FOR SELECT TO anon
  USING (
    is_active = true AND is_confirmed = true
    AND EXISTS (SELECT 1 FROM flyers f WHERE f.id = offers.flyer_id AND f.is_public = true)
  );

-- authenticated: active+confirmed OR own flyer
CREATE POLICY "offers_auth_read"
  ON public.offers FOR SELECT TO authenticated
  USING (
    (is_active = true AND is_confirmed = true)
    OR EXISTS (
      SELECT 1 FROM flyers f
      WHERE f.id = offers.flyer_id
        AND (f.is_public = true OR f.user_id = auth.uid())
    )
  );

-- Same fix for products_anon_read (depends on offers):
DROP POLICY "products_anon_read" ON public.products;
CREATE POLICY "products_anon_read"
  ON public.products FOR SELECT TO anon
  USING (
    EXISTS (
      SELECT 1 FROM offers o JOIN flyers f ON f.id = o.flyer_id
      WHERE o.product_id = products.id
        AND f.is_public = true AND o.is_active = true AND o.is_confirmed = true
    )
  );
