CREATE TABLE IF NOT EXISTS public.manager_supermarkets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  supermarket_id UUID NOT NULL REFERENCES public.supermarkets(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, supermarket_id)
);

CREATE INDEX IF NOT EXISTS idx_manager_supermarkets_user_id
  ON public.manager_supermarkets(user_id);

CREATE INDEX IF NOT EXISTS idx_manager_supermarkets_supermarket_id
  ON public.manager_supermarkets(supermarket_id);

INSERT INTO public.manager_supermarkets (user_id, supermarket_id)
SELECT id, managed_supermarket_id
FROM public.user_profiles
WHERE managed_supermarket_id IS NOT NULL
ON CONFLICT (user_id, supermarket_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.flyer_targets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  flyer_id UUID NOT NULL REFERENCES public.flyers(id) ON DELETE CASCADE,
  supermarket_id UUID NOT NULL REFERENCES public.supermarkets(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (flyer_id, supermarket_id)
);

CREATE INDEX IF NOT EXISTS idx_flyer_targets_flyer_id
  ON public.flyer_targets(flyer_id);

CREATE INDEX IF NOT EXISTS idx_flyer_targets_supermarket_id
  ON public.flyer_targets(supermarket_id);

ALTER TABLE public.flyers
  ADD COLUMN IF NOT EXISTS flyer_kind TEXT NOT NULL DEFAULT 'source'
    CHECK (flyer_kind IN ('source', 'published_target')),
  ADD COLUMN IF NOT EXISTS source_flyer_id UUID REFERENCES public.flyers(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_flyers_flyer_kind
  ON public.flyers(flyer_kind);

CREATE INDEX IF NOT EXISTS idx_flyers_source_flyer_id
  ON public.flyers(source_flyer_id)
  WHERE source_flyer_id IS NOT NULL;

UPDATE public.flyers
SET flyer_kind = CASE
  WHEN is_public = true THEN 'published_target'
  ELSE 'source'
END
WHERE flyer_kind IS DISTINCT FROM CASE
  WHEN is_public = true THEN 'published_target'
  ELSE 'source'
END;

INSERT INTO public.flyer_targets (flyer_id, supermarket_id)
SELECT id, supermarket_id
FROM public.flyers
WHERE flyer_kind = 'source'
  AND supermarket_id IS NOT NULL
ON CONFLICT (flyer_id, supermarket_id) DO NOTHING;
