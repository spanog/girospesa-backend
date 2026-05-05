-- Migration: create_products (catalogo canonale)
--
-- Decisione architetturale: products è il catalogo permanente dei prodotti.
-- I prezzi, le date di validità e i supermercati vivono in offers (migration 006).
-- Un prodotto non viene mai eliminato: product_id è stabile per tutta la vita dell'app.

CREATE TABLE products (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,         -- nome prodotto normalizzato
  brand       TEXT,                  -- marca
  category    TEXT,                  -- categoria normalizzata
  subcategory TEXT,
  format      JSONB NOT NULL DEFAULT '{}'::jsonb, -- formato strutturato canonico
  format_key  TEXT NOT NULL,         -- chiave canonica derivata da format
  format_label TEXT NOT NULL,        -- label leggibile derivata da format
  image_url   TEXT,                  -- URL immagine in Supabase Storage (bucket product-images)
  name_tsv    TSVECTOR,              -- colonna full-text search (auto-aggiornata dal trigger)
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (name, brand, format_key)  -- chiave naturale canonale
);

-- ── Indici ────────────────────────────────────────────────────────────────────
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_brand    ON products(brand);
CREATE INDEX idx_products_format_key ON products(format_key);
CREATE INDEX idx_products_name_tsv ON products USING gin(name_tsv);

-- ── Trigger: aggiornamento automatico name_tsv ────────────────────────────────
CREATE OR REPLACE FUNCTION products_update_tsv()
RETURNS TRIGGER AS $$
BEGIN
  NEW.name_tsv := to_tsvector('italian',
    coalesce(NEW.name,        '') || ' ' ||
    coalesce(NEW.brand,       '') || ' ' ||
    coalesce(NEW.category,    '') || ' ' ||
    coalesce(NEW.subcategory, '') || ' ' ||
    coalesce(NEW.format_label,'')
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER products_tsv_update
  BEFORE INSERT OR UPDATE ON products
  FOR EACH ROW EXECUTE FUNCTION products_update_tsv();

-- ── Row Level Security ────────────────────────────────────────────────────────
ALTER TABLE products ENABLE ROW LEVEL SECURITY;

-- authenticated: legge l'intero catalogo
-- (i prodotti sono un catalogo pubblico; il filtro per offerte attive/proprie
--  viene applicato sulla tabella offers, non qui)
CREATE POLICY "products_auth_read"
  ON products FOR SELECT
  TO authenticated
  USING (true);

-- anon: accesso ai prodotti con almeno un'offerta in un volantino pubblico
-- Policy aggiunta in 006_create_offers.sql (dipende dalla tabella offers)

-- INSERT/UPDATE/DELETE: solo service_role (pipeline FastAPI)
-- Nessuna policy per anon o authenticated → deny by default
