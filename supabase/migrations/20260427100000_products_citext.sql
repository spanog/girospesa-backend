-- Migration: products_citext
-- Makes name/brand/format comparisons case-insensitive so UNIQUE constraint
-- deduplicates regardless of AI extraction casing ("barilla" = "Barilla" = "BARILLA").

CREATE EXTENSION IF NOT EXISTS citext;

ALTER TABLE products
  ALTER COLUMN name   TYPE citext USING name::citext,
  ALTER COLUMN brand  TYPE citext USING brand::citext;
