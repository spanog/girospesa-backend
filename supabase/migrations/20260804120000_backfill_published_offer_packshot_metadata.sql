-- Existing published targets predate packshot metadata propagation. Re-running is safe.
UPDATE public.offers AS target
SET
  packshot_source_page = source.packshot_source_page,
  packshot_bbox = source.packshot_bbox
FROM public.offers AS source
WHERE target.source_offer_id = source.id
  AND target.offer_kind = 'published_target'
  AND source.packshot_bbox IS NOT NULL
  AND (
    target.packshot_source_page IS DISTINCT FROM source.packshot_source_page
    OR target.packshot_bbox IS DISTINCT FROM source.packshot_bbox
  );
