-- Resolve discovery branches inside PostgreSQL.
-- The previous PostgREST offer read stopped at its response-row limit before
-- distinct supermarket IDs were derived, hiding valid nearby branches.

CREATE INDEX IF NOT EXISTS idx_offers_public_current_supermarket
  ON public.offers (supermarket_id)
  INCLUDE (valid_from, valid_to)
  WHERE is_confirmed = true
    AND offer_kind = 'published_target';

CREATE OR REPLACE FUNCTION public.current_public_offer_supermarket_ids(
  candidate_supermarket_ids uuid[]
)
RETURNS TABLE(id uuid)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  SELECT candidate.supermarket_id AS id
  FROM unnest(candidate_supermarket_ids) AS candidate(supermarket_id)
  WHERE EXISTS (
    SELECT 1
    FROM public.offers AS offer
    WHERE offer.supermarket_id = candidate.supermarket_id
      AND offer.is_confirmed = true
      AND offer.offer_kind = 'published_target'
      AND (
        offer.valid_from IS NULL
        OR offer.valid_from <= (now() AT TIME ZONE 'Europe/Rome')::date
      )
      AND (
        offer.valid_to IS NULL
        OR offer.valid_to >= (now() AT TIME ZONE 'Europe/Rome')::date
      )
  );
$$;

REVOKE EXECUTE ON FUNCTION public.current_public_offer_supermarket_ids(uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_public_offer_supermarket_ids(uuid[]) TO service_role;
