-- Migration: create_favorites + user_profiles + push_subscriptions

-- ── favorites ─────────────────────────────────────────────────────────────────
-- product_id punta al catalogo canonale (products): i prodotti non vengono mai
-- eliminati, quindi nessun ON DELETE CASCADE. La domanda "ci sono offerte attive?"
-- si risolve con SELECT FROM offers WHERE product_id = ? AND is_active = true.
CREATE TABLE favorites (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  product_id UUID NOT NULL REFERENCES products(id),  -- FK senza cascade: prodotto permanente
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, product_id)
);

ALTER TABLE favorites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "favorites_own"
  ON favorites FOR ALL
  TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ── user_profiles ─────────────────────────────────────────────────────────────
CREATE TABLE user_profiles (
  id                       UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name             TEXT,
  avatar_url               TEXT,
  preferred_supermarkets   TEXT[],               -- slug array es. ["esselunga", "lidl"]

  -- Indirizzo di casa (obbligatorio alla registrazione — fallback per il raggio di ricerca)
  home_address             TEXT NOT NULL DEFAULT '',
  home_city                TEXT NOT NULL DEFAULT '',
  home_province            TEXT NOT NULL DEFAULT '',  -- es. "MI"
  home_postal_code         TEXT NOT NULL DEFAULT '',
  home_lat                 NUMERIC(10,7),         -- geocodificato dalla pipeline FastAPI
  home_lng                 NUMERIC(10,7),

  -- Punto di ricerca personalizzato (opzionale — override del punto casa)
  -- Se NULL, si usano home_lat/home_lng come punto di riferimento
  search_label             TEXT,                  -- es. "Ufficio", "Casa genitori"
  search_lat               NUMERIC(10,7),
  search_lng               NUMERIC(10,7),

  -- Raggio massimo supermercati (personalizzabile dall'utente)
  max_distance_km          INTEGER DEFAULT 10 CHECK (max_distance_km BETWEEN 1 AND 100),

  -- Preferenze notifiche
  notification_expiry      BOOLEAN DEFAULT true,  -- offerte in scadenza
  notification_deals       BOOLEAN DEFAULT true,  -- nuovi volantini
  notification_favorites   BOOLEAN DEFAULT true,  -- nuove offerte sui preferiti

  -- Consenso cookie analytics (NULL = non ancora chiesto, true = accettato, false = rifiutato)
  cookie_analytics_consent BOOLEAN DEFAULT NULL,

  plan                     TEXT DEFAULT 'free',   -- sempre 'free' — no paywall
  created_at               TIMESTAMPTZ DEFAULT now(),
  updated_at               TIMESTAMPTZ DEFAULT now()
);

CREATE TRIGGER user_profiles_updated_at
  BEFORE UPDATE ON user_profiles
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Auto-crea profilo al signup tramite trigger PostgreSQL
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO user_profiles (id, display_name)
  VALUES (
    NEW.id,
    NEW.raw_user_meta_data->>'display_name'
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_user();

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "profiles_own"
  ON user_profiles FOR ALL
  TO authenticated
  USING (id = auth.uid())
  WITH CHECK (id = auth.uid());

-- ── push_subscriptions ────────────────────────────────────────────────────────
CREATE TABLE push_subscriptions (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  endpoint   TEXT NOT NULL,
  p256dh     TEXT NOT NULL,
  auth_key   TEXT NOT NULL,
  user_agent TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, endpoint)
);

ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;

-- Solo authenticated: un utente gestisce solo le proprie sottoscrizioni push
CREATE POLICY "push_subscriptions_own"
  ON push_subscriptions FOR ALL
  TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
