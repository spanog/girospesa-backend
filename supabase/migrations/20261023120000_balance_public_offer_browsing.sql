-- Balanced browsing is computed across all filtered representatives before paging.
-- Stable at fixed data and Rome calendar day; no random/session-dependent shuffle.

CREATE OR REPLACE FUNCTION public.nearby_public_offer_page(
  candidate_supermarket_ids UUID[],
  candidate_distances_km DOUBLE PRECISION[],
  filter_query TEXT DEFAULT NULL,
  filter_category TEXT DEFAULT NULL,
  filter_subcategory TEXT DEFAULT NULL,
  filter_supermarket_id UUID DEFAULT NULL,
  filter_supermarket_ids UUID[] DEFAULT NULL,
  page_limit INTEGER DEFAULT 20,
  page_offset INTEGER DEFAULT 0
)
RETURNS TABLE(
  id UUID,
  distance_km DOUBLE PRECISION,
  total BIGINT,
  supermarket_count BIGINT,
  counts_by_supermarket_id JSONB,
  counts_by_supermarket_slug JSONB
)
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
  WITH candidates AS (
    SELECT candidate.id, candidate.distance_km
    FROM unnest(candidate_supermarket_ids, candidate_distances_km)
      AS candidate(id, distance_km)
    UNION ALL
    -- Authenticated profiles without a locality retain the global fallback.
    SELECT supermarket.id, NULL::double precision
    FROM public.supermarkets AS supermarket
    WHERE candidate_supermarket_ids IS NULL
  ),
  query_terms AS (
    SELECT NULLIF(btrim(filter_query), '') AS raw_query
  ),
  scoped_offers AS NOT MATERIALIZED (
    SELECT
      offer.id,
      offer.name,
      offer.supermarket_id,
      candidate.distance_km,
      query_terms.raw_query,
      public.offer_search_document(
        offer.name,
        offer.brand,
        offer.category,
        offer.subcategory,
        offer.format_label
      ) AS search_document,
      CASE
        WHEN query_terms.raw_query IS NULL THEN NULL
        ELSE websearch_to_tsquery('italian', query_terms.raw_query)
      END AS semantic_query
    FROM public.offers AS offer
    JOIN candidates AS candidate ON candidate.id = offer.supermarket_id
    CROSS JOIN query_terms
    WHERE offer.is_confirmed = true
      AND offer.offer_kind = 'published_target'
      AND (
        offer.valid_from IS NULL
        OR offer.valid_from <= (now() AT TIME ZONE 'Europe/Rome')::date
      )
      AND (
        offer.valid_to IS NULL
        OR offer.valid_to >= (now() AT TIME ZONE 'Europe/Rome')::date
      )
      AND (filter_category IS NULL OR offer.category = filter_category)
      AND (filter_subcategory IS NULL OR offer.subcategory = filter_subcategory)
      AND (filter_supermarket_id IS NULL OR offer.supermarket_id = filter_supermarket_id)
      AND (
        COALESCE(cardinality(filter_supermarket_ids), 0) = 0
        OR offer.supermarket_id = ANY(filter_supermarket_ids)
      )
  ),
  direct_matches AS NOT MATERIALIZED (
    SELECT
      scoped_offers.*,
      CASE
        WHEN raw_query IS NULL THEN 0::real
        WHEN search_document @@ semantic_query
          THEN ts_rank_cd(search_document, semantic_query)
        ELSE 0::real
      END AS relevance
    FROM scoped_offers
    WHERE raw_query IS NULL
      OR search_document @@ semantic_query
      OR name ILIKE '%' || raw_query || '%'
  ),
  fuzzy_matches AS (
    SELECT
      scoped_offers.*,
      extensions.word_similarity(lower(raw_query), lower(name))::real AS relevance
    FROM scoped_offers
    WHERE raw_query IS NOT NULL
      AND char_length(raw_query) >= 5
      AND NOT EXISTS (SELECT 1 FROM direct_matches)
      AND extensions.word_similarity(lower(raw_query), lower(name)) >= 0.5
  ),
  search_matches AS (
    SELECT * FROM direct_matches
    UNION ALL
    SELECT * FROM fuzzy_matches
  ),
  ranked_offers AS (
    SELECT
      search_match.id,
      search_match.name,
      search_match.supermarket_id,
      search_match.distance_km,
      search_match.relevance,
      search_match.raw_query,
      offer.category,
      offer.valid_from,
      -- Conservative display signature only: never merge independent offers.
      jsonb_build_array(
        lower(regexp_replace(btrim(offer.name), '\s+', ' ', 'g')),
        lower(regexp_replace(btrim(COALESCE(offer.brand, '')), '\s+', ' ', 'g')),
        COALESCE(offer.format_key, '')
      ) AS display_signature,
      row_number() OVER (
        PARTITION BY COALESCE(offer.source_offer_id, offer.id)
        ORDER BY
          search_match.distance_km ASC NULLS LAST,
          search_match.supermarket_id,
          search_match.id
      ) AS representative_rank
    FROM search_matches AS search_match
    JOIN public.offers AS offer ON offer.id = search_match.id
  ),
  representatives AS (
    SELECT id, name, supermarket_id, distance_km, relevance,
      raw_query, category, valid_from, display_signature
    FROM ranked_offers
    WHERE representative_rank = 1
  ),
  per_supermarket AS (
    SELECT
      representative.supermarket_id,
      supermarket.slug,
      count(*)::BIGINT AS offer_count
    FROM representatives AS representative
    JOIN public.supermarkets AS supermarket ON supermarket.id = representative.supermarket_id
    GROUP BY representative.supermarket_id, supermarket.slug
  ),
  summary AS (
    SELECT
      (SELECT count(*)::BIGINT FROM representatives) AS total,
      (SELECT count(*)::BIGINT FROM per_supermarket) AS supermarket_count,
      COALESCE(
        (
          SELECT jsonb_object_agg(supermarket_id::TEXT, offer_count)
          FROM per_supermarket
        ),
        '{}'::JSONB
      ) AS counts_by_supermarket_id,
      COALESCE(
        (
          SELECT jsonb_object_agg(slug, offer_count)
          FROM per_supermarket
          WHERE slug IS NOT NULL
        ),
        '{}'::JSONB
      ) AS counts_by_supermarket_slug
  ),
  display_passes AS (
    -- Show one occurrence of each exact display signature before later copies.
    SELECT *, row_number() OVER (
      PARTITION BY display_signature
      ORDER BY
        (valid_from >= (now() AT TIME ZONE 'Europe/Rome')::date - 6) DESC NULLS LAST,
        md5(id::text), id
    ) AS display_pass
    FROM representatives
    WHERE raw_query IS NULL
  ),
  category_turns AS (
    -- Recent means started within seven calendar days, not recently extracted.
    -- Freshness only affects a store/category lane, so it cannot flood the feed.
    SELECT *, row_number() OVER (
      PARTITION BY display_pass, supermarket_id, category
      ORDER BY
        COALESCE(valid_from >= (now() AT TIME ZONE 'Europe/Rome')::date - 6, false) DESC,
        md5(id::text), id
    ) AS category_turn
    FROM display_passes
  ),
  store_turns AS (
    SELECT *, row_number() OVER (
      PARTITION BY display_pass, supermarket_id
      ORDER BY category_turn, md5(COALESCE(category, '')), id
    ) AS store_turn
    FROM category_turns
  ),
  ordered_offers AS (
    SELECT id, distance_km, row_number() OVER (
      ORDER BY display_pass, store_turn, md5(supermarket_id::text), id
    ) AS page_order
    FROM store_turns
    UNION ALL
    -- Preserve search relevance and its existing alphabetical tie-breaker.
    SELECT id, distance_km, row_number() OVER (
      ORDER BY relevance DESC, COALESCE(lower(name), ''), id
    ) AS page_order
    FROM representatives
    WHERE raw_query IS NOT NULL
  ),
  page AS (
    SELECT id, distance_km, page_order
    FROM ordered_offers
    ORDER BY page_order
    LIMIT GREATEST(page_limit, 0)
    OFFSET GREATEST(page_offset, 0)
  )
  SELECT
    page.id,
    page.distance_km,
    summary.total,
    summary.supermarket_count,
    summary.counts_by_supermarket_id,
    summary.counts_by_supermarket_slug
  FROM summary
  LEFT JOIN page ON true
  ORDER BY page.page_order NULLS LAST;
$$;

REVOKE EXECUTE ON FUNCTION public.nearby_public_offer_page(
  UUID[], DOUBLE PRECISION[], TEXT, TEXT, TEXT, UUID, UUID[], INTEGER, INTEGER
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.nearby_public_offer_page(
  UUID[], DOUBLE PRECISION[], TEXT, TEXT, TEXT, UUID, UUID[], INTEGER, INTEGER
) TO service_role;

NOTIFY pgrst, 'reload schema';
