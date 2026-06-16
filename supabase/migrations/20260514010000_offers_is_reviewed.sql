ALTER TABLE public.offers
  ADD COLUMN is_reviewed BOOLEAN NOT NULL DEFAULT false;
