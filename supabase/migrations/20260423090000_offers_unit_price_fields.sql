ALTER TABLE public.offers
  ADD COLUMN unit_price_value NUMERIC(8,2),
  ADD COLUMN unit_price_unit TEXT;

ALTER TABLE public.offers
  ADD CONSTRAINT offers_unit_price_unit_check
  CHECK (
    unit_price_unit IS NULL
    OR unit_price_unit IN ('kg', 'l', 'kg sgocc')
  );

CREATE INDEX idx_offers_unit_price_value
  ON public.offers(unit_price_unit, unit_price_value);
