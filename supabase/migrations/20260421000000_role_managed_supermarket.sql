ALTER TABLE public.user_profiles
  ADD COLUMN role TEXT NOT NULL DEFAULT 'customer'
    CHECK (role IN ('customer', 'supermarket_manager', 'admin'));

ALTER TABLE public.user_profiles
  ADD COLUMN managed_supermarket_id UUID
    REFERENCES public.supermarkets(id) ON DELETE SET NULL;

ALTER TABLE public.user_profiles
  ADD CONSTRAINT chk_manager_needs_supermarket CHECK (
    (role = 'supermarket_manager' AND managed_supermarket_id IS NOT NULL)
    OR (role != 'supermarket_manager')
  );

CREATE INDEX idx_user_profiles_role ON public.user_profiles(role);
CREATE INDEX idx_user_profiles_managed_supermarket
  ON public.user_profiles(managed_supermarket_id)
  WHERE managed_supermarket_id IS NOT NULL;
