-- Migration: create_offers (istanze promozionali temporali)
--
-- Ogni riga è un'offerta: un prodotto (product_id) messo in promozione
-- da un supermercato (supermarket_id) in un dato volantino (flyer_id).
-- La pipeline FastAPI upserta products su (name, brand, format_key) poi inserisce offers.
-- La colonna is_active è generata automaticamente: non aggiornare manualmente.

CREATE TABLE offers (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id       UUID NOT NULL REFERENCES products(id),
  supermarket_id   UUID NOT NULL REFERENCES supermarkets(id),
  supermarket_name TEXT,                         -- snapshot denormalizzato per query veloci
  flyer_id         UUID REFERENCES flyers(id) ON DELETE SET NULL,
  price_original   NUMERIC(8,2),                -- prezzo pieno
  price_offer      NUMERIC(8,2) NOT NULL,       -- prezzo in offerta
  discount_pct     INTEGER,                     -- calcolato dal trigger
  unit_price       TEXT,                        -- es. "€2,40/kg"
  offer_type       TEXT,                        -- es. "3x2", "sconto diretto", "punti"
  offer_notes      TEXT,                        -- es. "solo card soci"
  valid_from       DATE,
  valid_to         DATE,
  -- is_active è impostato dal trigger su INSERT/UPDATE; per query real-time
  -- usare "valid_to >= CURRENT_DATE" che è sempre preciso.
  is_active        BOOLEAN,
  raw_text         TEXT,                        -- testo grezzo estratto dall'AI
  confidence_score FLOAT,                       -- confidenza estrazione AI (0-1)
  created_at       TIMESTAMPTZ DEFAULT now()
);

-- ── Indici ────────────────────────────────────────────────────────────────────
CREATE INDEX idx_offers_product_id        ON offers(product_id);
CREATE INDEX idx_offers_supermarket_id    ON offers(supermarket_id);
CREATE INDEX idx_offers_flyer_id          ON offers(flyer_id);
CREATE INDEX idx_offers_valid_to          ON offers(valid_to);
CREATE INDEX idx_offers_is_active         ON offers(is_active);
CREATE INDEX idx_offers_discount_pct      ON offers(discount_pct DESC NULLS LAST);
CREATE INDEX idx_offers_product_store     ON offers(product_id, supermarket_id);

-- ── Trigger: calcolo automatico discount_pct ─────────────────────────────────
CREATE OR REPLACE FUNCTION offers_compute_fields()
RETURNS TRIGGER AS $$
BEGIN
  -- Calcola discount_pct
  IF NEW.price_original IS NOT NULL AND NEW.price_original > 0 THEN
    NEW.discount_pct := ROUND(
      ((NEW.price_original - NEW.price_offer) / NEW.price_original) * 100
    );
  ELSE
    NEW.discount_pct := NULL;
  END IF;

  -- Calcola is_active (CURRENT_DATE non è immutabile → non usabile come GENERATED ALWAYS)
  IF NEW.valid_to IS NOT NULL THEN
    NEW.is_active := NEW.valid_to >= CURRENT_DATE;
  ELSE
    NEW.is_active := true;  -- nessuna scadenza → sempre attiva
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER offers_compute_fields
  BEFORE INSERT OR UPDATE ON offers
  FOR EACH ROW EXECUTE FUNCTION offers_compute_fields();

-- ── Row Level Security ────────────────────────────────────────────────────────
ALTER TABLE offers ENABLE ROW LEVEL SECURITY;

-- anon: solo offerte attive in volantini pubblici
CREATE POLICY "offers_anon_read"
  ON offers FOR SELECT
  TO anon
  USING (
    is_active = true
    AND EXISTS (
      SELECT 1 FROM flyers f
      WHERE f.id = offers.flyer_id AND f.is_public = true
    )
  );

-- authenticated: tutte le offerte attive + offerte dei propri volantini
CREATE POLICY "offers_auth_read"
  ON offers FOR SELECT
  TO authenticated
  USING (
    is_active = true
    OR EXISTS (
      SELECT 1 FROM flyers f
      WHERE f.id = offers.flyer_id
        AND (f.is_public = true OR f.user_id = auth.uid())
    )
  );

-- INSERT/UPDATE/DELETE: solo service_role (pipeline FastAPI)
-- Nessuna policy per anon o authenticated su scrittura → deny by default

-- ── Policy prodotti per anon (dipende da offers) ──────────────────────────────
-- Aggiunta qui perché la migration 003 non aveva ancora la tabella offers.
CREATE POLICY "products_anon_read"
  ON products FOR SELECT
  TO anon
  USING (
    EXISTS (
      SELECT 1 FROM offers o
      JOIN flyers f ON f.id = o.flyer_id
      WHERE o.product_id = products.id
        AND f.is_public = true
        AND o.is_active = true
    )
  );
