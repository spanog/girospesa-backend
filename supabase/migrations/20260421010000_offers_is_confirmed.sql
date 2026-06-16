ALTER TABLE public.offers ADD COLUMN is_confirmed BOOLEAN NOT NULL DEFAULT false;
UPDATE public.offers SET is_confirmed = true;
CREATE INDEX idx_offers_flyer_confirmed ON public.offers(flyer_id, is_confirmed);
