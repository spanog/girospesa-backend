-- Migration: products_soft_delete
--
-- Adds is_archived column to products for soft-delete support.
-- Archived products stay in the DB (product_id remains stable) but disappear
-- from public queries (anon + authenticated SELECT policies updated below).

ALTER TABLE products ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX products_is_archived_idx ON products(is_archived);

-- ── Update existing RLS policies to exclude archived products ─────────────────

-- Policy "products_auth_read" was created in 003_create_products.sql.
DROP POLICY IF EXISTS "products_auth_read" ON products;
CREATE POLICY "products_auth_read"
  ON products FOR SELECT
  TO authenticated
  USING (is_archived = false);

-- Policy "products_anon_read" was created in 006_create_offers.sql.
DROP POLICY IF EXISTS "products_anon_read" ON products;
CREATE POLICY "products_anon_read"
  ON products FOR SELECT
  TO anon
  USING (
    is_archived = false
    AND EXISTS (
      SELECT 1 FROM offers o
      JOIN flyers f ON f.id = o.flyer_id
      WHERE o.product_id = products.id
        AND f.is_public = true
        AND o.is_active = true
    )
  );
