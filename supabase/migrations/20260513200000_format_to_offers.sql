-- Migration: format_to_offers
--
-- Moves format/format_key/format_label from products to offers.
-- products are now uniquely identified by (name, brand).
-- Format is an attribute of an offer (a specific promotional instance),
-- not of the canonical product.

BEGIN;

-- ── Step 1: Add format columns to offers ─────────────────────────────────────
ALTER TABLE offers
  ADD COLUMN format       JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN format_key   TEXT  NOT NULL DEFAULT 'v1:{}',
  ADD COLUMN format_label TEXT  NOT NULL DEFAULT '';

-- ── Step 2: Backfill format from linked products ──────────────────────────────
UPDATE offers o
SET
  format       = p.format,
  format_key   = p.format_key,
  format_label = p.format_label
FROM products p
WHERE o.product_id = p.id;

-- ── Step 3: Unique index on offers to prevent duplicate extraction per flyer ──
-- NULL flyer_id (manual offers) are excluded — multiple manual offers allowed.
CREATE UNIQUE INDEX idx_offers_product_flyer_format
  ON offers(product_id, flyer_id, format_key)
  WHERE flyer_id IS NOT NULL;

-- ── Step 4: Merge products with same (name, brand) ───────────────────────────
-- Winner = product with most linked offers; tiebreak = oldest created_at.
-- Reassign all FKs from losers to winner, then delete losers.
DO $$
DECLARE
  rec RECORD;
  new_items JSONB;
  item JSONB;
  deal JSONB;
  new_deals JSONB;
  mapped_id TEXT;
BEGIN
  -- Build temp mapping: loser_id → winner_id for duplicate (name, brand) groups
  CREATE TEMP TABLE _product_id_map ON COMMIT DROP AS
  WITH offer_counts AS (
    SELECT product_id, count(*) AS n FROM offers GROUP BY product_id
  ),
  ranked AS (
    SELECT
      p.id,
      p.name,
      p.brand,
      row_number() OVER (
        PARTITION BY p.name, p.brand
        ORDER BY COALESCE(oc.n, 0) DESC, p.created_at ASC
      ) AS rn
    FROM products p
    LEFT JOIN offer_counts oc ON oc.product_id = p.id
  ),
  winners AS (SELECT id AS winner_id, name, brand FROM ranked WHERE rn = 1)
  SELECT r.id::text AS loser_id, w.winner_id::text
  FROM ranked r
  JOIN winners w
    ON w.name = r.name
    AND (
      (w.brand IS NULL AND r.brand IS NULL)
      OR w.brand::text = r.brand::text
    )
  WHERE r.rn > 1;

  -- Reassign offers
  UPDATE offers o
  SET product_id = m.winner_id::uuid
  FROM _product_id_map m
  WHERE o.product_id::text = m.loser_id;

  -- Reassign favorites (deduplicate: winner may already have a favorite)
  UPDATE favorites f
  SET product_id = m.winner_id::uuid
  FROM _product_id_map m
  WHERE f.product_id::text = m.loser_id
    AND NOT EXISTS (
      SELECT 1 FROM favorites f2
      WHERE f2.user_id = f.user_id AND f2.product_id = m.winner_id::uuid
    );
  DELETE FROM favorites f
  USING _product_id_map m
  WHERE f.product_id::text = m.loser_id;

  -- Update shopping_lists.items JSONB: pinned_product_id + DealSnapshot.product_id
  FOR rec IN
    SELECT id, items FROM shopping_lists
    WHERE items IS NOT NULL AND jsonb_array_length(items) > 0
  LOOP
    new_items := '[]'::jsonb;
    FOR item IN SELECT value FROM jsonb_array_elements(rec.items) LOOP
      -- Update pinned_product_id
      SELECT winner_id INTO mapped_id
      FROM _product_id_map
      WHERE loser_id = item->>'pinned_product_id';
      IF FOUND THEN
        item := jsonb_set(item, '{pinned_product_id}', to_jsonb(mapped_id));
      END IF;

      -- Update found_deals[*].product_id
      IF item ? 'found_deals' AND jsonb_typeof(item->'found_deals') = 'array' THEN
        new_deals := '[]'::jsonb;
        FOR deal IN SELECT value FROM jsonb_array_elements(item->'found_deals') LOOP
          SELECT winner_id INTO mapped_id
          FROM _product_id_map
          WHERE loser_id = deal->>'product_id';
          IF FOUND THEN
            deal := jsonb_set(deal, '{product_id}', to_jsonb(mapped_id));
          END IF;
          new_deals := new_deals || jsonb_build_array(deal);
        END LOOP;
        item := jsonb_set(item, '{found_deals}', new_deals);
      END IF;

      new_items := new_items || jsonb_build_array(item);
    END LOOP;
    UPDATE shopping_lists SET items = new_items WHERE id = rec.id;
  END LOOP;

  -- Delete loser products (all FK deps now point to winners)
  DELETE FROM products
  WHERE id::text IN (SELECT loser_id FROM _product_id_map);
END;
$$;

-- ── Step 5: Update search_products_catalog RPC (remove format columns) ────────
-- Must DROP first: cannot change return type via CREATE OR REPLACE.
DROP FUNCTION IF EXISTS public.search_products_catalog(text, integer);
CREATE FUNCTION public.search_products_catalog(query text, lim integer DEFAULT 10)
RETURNS TABLE (
  id uuid,
  name text,
  brand text,
  category text,
  subcategory text,
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

-- ── Step 6: Drop format columns and index from products ───────────────────────
DROP INDEX IF EXISTS idx_products_format_key;

ALTER TABLE products
  DROP COLUMN format,
  DROP COLUMN format_key,
  DROP COLUMN format_label;

-- ── Step 7: Update TSV trigger — format_label no longer on products ───────────
CREATE OR REPLACE FUNCTION products_update_tsv()
RETURNS TRIGGER AS $$
BEGIN
  NEW.name_tsv := to_tsvector('italian',
    coalesce(NEW.name,        '') || ' ' ||
    coalesce(NEW.brand,       '') || ' ' ||
    coalesce(NEW.category,    '') || ' ' ||
    coalesce(NEW.subcategory, '')
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── Step 8: Change UNIQUE constraint on products to (name, brand) ─────────────
ALTER TABLE products DROP CONSTRAINT IF EXISTS products_name_brand_format_key_key;
ALTER TABLE products ADD CONSTRAINT products_name_brand_key
  UNIQUE NULLS NOT DISTINCT (name, brand);

COMMIT;
