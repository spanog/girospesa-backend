-- Add explicit deny-all RLS policies for internal-only tables.
-- Service role still bypasses RLS; anon/authenticated clients stay blocked.

DROP POLICY IF EXISTS "Anyone can insert flyer requests" ON public.flyer_requests;

DROP POLICY IF EXISTS analytics_data_deny_all ON public.analytics_data;
CREATE POLICY analytics_data_deny_all
  ON public.analytics_data
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);

DROP POLICY IF EXISTS extraction_log_deny_all ON public.extraction_log;
CREATE POLICY extraction_log_deny_all
  ON public.extraction_log
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);

DROP POLICY IF EXISTS flyer_requests_deny_all ON public.flyer_requests;
CREATE POLICY flyer_requests_deny_all
  ON public.flyer_requests
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);
