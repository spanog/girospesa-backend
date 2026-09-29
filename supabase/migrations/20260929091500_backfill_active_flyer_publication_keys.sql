WITH target_sets AS (
  SELECT
    flyer_id,
    string_agg(DISTINCT supermarket_id::text, '|' ORDER BY supermarket_id::text) AS target_key
  FROM public.flyer_targets
  GROUP BY flyer_id
)
UPDATE public.flyers AS flyer
SET publication_key = target_sets.target_key || '|' || flyer.valid_from::text || '|' || flyer.valid_to::text
FROM target_sets
WHERE flyer.id = target_sets.flyer_id
  AND flyer.flyer_kind = 'source'
  AND flyer.valid_from IS NOT NULL
  AND flyer.valid_to IS NOT NULL
  AND flyer.valid_to >= CURRENT_DATE
  AND flyer.publication_key IS NULL;
