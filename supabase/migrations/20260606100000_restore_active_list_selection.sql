ALTER TABLE public.user_profiles
  ADD COLUMN IF NOT EXISTS active_list_id UUID REFERENCES public.shopping_lists(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_user_profiles_active_list_id
  ON public.user_profiles(active_list_id);

UPDATE public.user_profiles up
SET active_list_id = sl.id
FROM public.shopping_lists sl
WHERE sl.user_id = up.id
  AND up.active_list_id IS NULL;
