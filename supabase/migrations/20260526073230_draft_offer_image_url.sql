ALTER TABLE public.offers
ADD COLUMN IF NOT EXISTS draft_image_url TEXT;
