CREATE OR REPLACE FUNCTION public.search_products_catalog(query text, lim integer DEFAULT 10)
RETURNS TABLE (
  id uuid,
  name text,
  brand text,
  category text,
  subcategory text,
  format jsonb,
  format_label text,
  image_url text,
  score float
)
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
  WITH normalized_query AS (
    SELECT lower(btrim(query)) AS q
  )
  SELECT
    p.id,
    p.name,
    p.brand,
    p.category,
    p.subcategory,
    p.format,
    p.format_label,
    p.image_url,
    (
      extensions.word_similarity(nq.q, p.name) * 0.6
      + COALESCE(extensions.word_similarity(nq.q, p.brand), 0) * 0.4
      + CASE
          WHEN lower(p.name) = nq.q THEN 1.00
          WHEN lower(p.name) LIKE nq.q || '%' THEN 0.90
          WHEN lower(p.name) LIKE '% ' || nq.q || '%' THEN 0.80
          WHEN position(nq.q IN lower(p.name)) > 0 THEN 0.60
          ELSE 0
        END
      + CASE
          WHEN lower(COALESCE(p.brand, '')) = nq.q THEN 0.70
          WHEN lower(COALESCE(p.brand, '')) LIKE nq.q || '%' THEN 0.55
          WHEN lower(COALESCE(p.brand, '')) LIKE '% ' || nq.q || '%' THEN 0.45
          WHEN position(nq.q IN lower(COALESCE(p.brand, ''))) > 0 THEN 0.30
          ELSE 0
        END
    ) AS score
  FROM public.products AS p
  CROSS JOIN normalized_query AS nq
  WHERE
    nq.q <> ''
    AND (
      lower(p.name) LIKE nq.q || '%'
      OR lower(p.name) LIKE '% ' || nq.q || '%'
      OR position(nq.q IN lower(p.name)) > 0
      OR lower(COALESCE(p.brand, '')) LIKE nq.q || '%'
      OR lower(COALESCE(p.brand, '')) LIKE '% ' || nq.q || '%'
      OR position(nq.q IN lower(COALESCE(p.brand, ''))) > 0
      OR nq.q OPERATOR(extensions.<<%) p.name
      OR nq.q OPERATOR(extensions.<<%) p.brand
    )
  ORDER BY score DESC, p.name ASC
  LIMIT lim;
$$;
