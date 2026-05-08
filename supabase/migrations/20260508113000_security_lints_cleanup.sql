-- Hardens Supabase advisor findings without changing app-facing REST/RPC flows.

CREATE SCHEMA IF NOT EXISTS extensions;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_extension
    WHERE extname = 'citext'
      AND extnamespace <> 'extensions'::regnamespace
  ) THEN
    ALTER EXTENSION citext SET SCHEMA extensions;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_extension
    WHERE extname = 'pg_trgm'
      AND extnamespace <> 'extensions'::regnamespace
  ) THEN
    ALTER EXTENSION pg_trgm SET SCHEMA extensions;
  END IF;
END $$;

DROP EXTENSION IF EXISTS pg_graphql;

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
      extensions.word_similarity(query, p.name) * 0.6
      + COALESCE(extensions.word_similarity(query, p.brand), 0) * 0.4
    ) AS score
  FROM public.products AS p
  WHERE
    query OPERATOR(extensions.<<%) p.name
    OR query OPERATOR(extensions.<<%) p.brand
  ORDER BY score DESC
  LIMIT lim;
$$;

ALTER FUNCTION public.offer_is_currently_active(date, date) SET search_path = public;
ALTER FUNCTION public.offers_compute_fields() SET search_path = public;

DROP POLICY IF EXISTS "avatars_read_public" ON storage.objects;
DROP POLICY IF EXISTS "logos_read_public" ON storage.objects;
DROP POLICY IF EXISTS "product_images_read_public" ON storage.objects;

REVOKE EXECUTE ON FUNCTION public.handle_new_user() FROM public, anon, authenticated;

REVOKE EXECUTE ON FUNCTION public.create_list(text) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_list(text) TO authenticated;

REVOKE EXECUTE ON FUNCTION public.update_list_item(uuid, text, jsonb) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_list_item(uuid, text, jsonb) TO authenticated;
