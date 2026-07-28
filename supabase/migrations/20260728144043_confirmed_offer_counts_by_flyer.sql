CREATE OR REPLACE FUNCTION public.count_offers_by_flyer(
  p_flyer_ids uuid[],
  p_is_confirmed boolean
)
RETURNS TABLE (flyer_id uuid, offer_count bigint)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
  SELECT offers.flyer_id, COUNT(*)::bigint
  FROM public.offers
  WHERE offers.flyer_id = ANY(p_flyer_ids)
    AND offers.is_confirmed = p_is_confirmed
  GROUP BY offers.flyer_id;
$$;

REVOKE ALL ON FUNCTION public.count_offers_by_flyer(uuid[], boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.count_offers_by_flyer(uuid[], boolean) TO service_role;
