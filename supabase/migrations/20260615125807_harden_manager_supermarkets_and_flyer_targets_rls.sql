ALTER TABLE public.manager_supermarkets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.flyer_targets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "manager_supermarkets_deny_all" ON public.manager_supermarkets;
CREATE POLICY "manager_supermarkets_deny_all"
  ON public.manager_supermarkets
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);

DROP POLICY IF EXISTS "flyer_targets_deny_all" ON public.flyer_targets;
CREATE POLICY "flyer_targets_deny_all"
  ON public.flyer_targets
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);
