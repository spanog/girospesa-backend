-- Migration: create_supermarkets
-- Creates the supermarkets table (one row = one store branch)

CREATE TABLE supermarkets (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  slug        TEXT UNIQUE NOT NULL,
  logo_url    TEXT,
  color_hex   TEXT,
  website_url TEXT,
  address     TEXT,
  city        TEXT,
  province    TEXT,
  postal_code TEXT,
  lat         NUMERIC(10,7),
  lng         NUMERIC(10,7),
  is_active   BOOLEAN DEFAULT true,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- RLS: public read, only service_role can write
ALTER TABLE supermarkets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "supermarkets_read_all"
  ON supermarkets FOR SELECT
  USING (true);

