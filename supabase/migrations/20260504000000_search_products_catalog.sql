-- pg_trgm extension for fuzzy product catalog search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Trigram GIN indexes for fast word_similarity on name and brand
CREATE INDEX IF NOT EXISTS products_name_trgm ON products USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS products_brand_trgm ON products USING GIN (brand gin_trgm_ops);

-- Product catalog search: fuzzy match on name+brand, ranked by word_similarity score.
-- Used by admin/manager autocomplete when creating manual offers.
-- query <<% name  → true when word_similarity(query, name) > pg_trgm.word_similarity_threshold (default 0.3)
CREATE OR REPLACE FUNCTION search_products_catalog(query text, lim integer DEFAULT 10)
RETURNS TABLE (
  id          uuid,
  name        text,
  brand       text,
  category    text,
  subcategory text,
  format      jsonb,
  format_label text,
  image_url   text,
  score       float
)
LANGUAGE sql
STABLE
AS $$
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
      word_similarity(query, p.name) * 0.6
      + COALESCE(word_similarity(query, p.brand), 0) * 0.4
    ) AS score
  FROM products p
  WHERE query <<% p.name OR query <<% p.brand
  ORDER BY score DESC
  LIMIT lim;
$$;
