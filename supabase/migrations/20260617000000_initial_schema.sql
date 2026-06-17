-- Baseline initial schema for first production deployment.
-- Generated on 2026-06-17 by concatenating the historical local migration chain.


-- Source: supabase/migrations/001_create_supermarkets.sql

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



-- Source: supabase/migrations/002_create_flyers.sql

-- Migration: create_flyers

CREATE TABLE flyers (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  supermarket_id       UUID REFERENCES supermarkets(id),
  supermarket_name     TEXT,
  file_url             TEXT NOT NULL,
  file_type            TEXT NOT NULL CHECK (file_type IN ('pdf', 'image')),
  file_name            TEXT,
  valid_from           DATE,
  valid_to             DATE,
  status               TEXT DEFAULT 'pending' CHECK (status IN ('pending','processing','done','error')),
  error_message        TEXT,
  products_count       INTEGER DEFAULT 0,
  pages_count          INTEGER DEFAULT 0,
  extraction_metadata  JSONB,
  is_public            BOOLEAN DEFAULT false,
  created_at           TIMESTAMPTZ DEFAULT now(),
  updated_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_flyers_user_id       ON flyers(user_id);
CREATE INDEX idx_flyers_supermarket   ON flyers(supermarket_id);
CREATE INDEX idx_flyers_status        ON flyers(status);
CREATE INDEX idx_flyers_valid_to      ON flyers(valid_to);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER flyers_updated_at
  BEFORE UPDATE ON flyers
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- RLS
ALTER TABLE flyers ENABLE ROW LEVEL SECURITY;

-- anon: only public flyers
CREATE POLICY "flyers_anon_read_public"
  ON flyers FOR SELECT
  TO anon
  USING (is_public = true);

-- authenticated: own flyers + public
CREATE POLICY "flyers_auth_read"
  ON flyers FOR SELECT
  TO authenticated
  USING (user_id = auth.uid() OR is_public = true);

CREATE POLICY "flyers_auth_insert"
  ON flyers FOR INSERT
  TO authenticated
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "flyers_auth_update"
  ON flyers FOR UPDATE
  TO authenticated
  USING (user_id = auth.uid());

CREATE POLICY "flyers_auth_delete"
  ON flyers FOR DELETE
  TO authenticated
  USING (user_id = auth.uid());


-- Source: supabase/migrations/003_create_products.sql

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


-- Source: supabase/migrations/004_create_shopping_lists.sql

-- Migration: create_shopping_lists + list_members + list_invites

-- gen_random_bytes richiede pgcrypto
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- shopping_lists
CREATE TABLE shopping_lists (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  name       TEXT DEFAULT 'Lista spesa',
  items      JSONB NOT NULL DEFAULT '[]',
  is_active  BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_shopping_lists_user_id ON shopping_lists(user_id);

CREATE TRIGGER shopping_lists_updated_at
  BEFORE UPDATE ON shopping_lists
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- list_members
CREATE TABLE list_members (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  list_id    UUID NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
  user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role       TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','member')),
  invited_by UUID REFERENCES auth.users(id),
  joined_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE(list_id, user_id)
);

CREATE INDEX idx_list_members_list_id ON list_members(list_id);
CREATE INDEX idx_list_members_user_id ON list_members(user_id);

-- list_invites
CREATE TABLE list_invites (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  list_id     UUID NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
  invited_by  UUID NOT NULL REFERENCES auth.users(id),
  token       TEXT UNIQUE NOT NULL DEFAULT replace(gen_random_uuid()::text,'-','') || replace(gen_random_uuid()::text,'-',''),
  email       TEXT,
  status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','expired','revoked')),
  expires_at  TIMESTAMPTZ NOT NULL DEFAULT now() + interval '7 days',
  accepted_at TIMESTAMPTZ,
  accepted_by UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_list_invites_token   ON list_invites(token);
CREATE INDEX idx_list_invites_list_id ON list_invites(list_id);

-- RPC: atomically create list + owner membership
CREATE OR REPLACE FUNCTION create_list(p_name TEXT)
RETURNS UUID AS $$
DECLARE
  v_list_id UUID;
BEGIN
  INSERT INTO shopping_lists (user_id, name)
  VALUES (auth.uid(), p_name)
  RETURNING id INTO v_list_id;

  INSERT INTO list_members (list_id, user_id, role)
  VALUES (v_list_id, auth.uid(), 'owner');

  RETURN v_list_id;
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;

REVOKE EXECUTE ON FUNCTION public.create_list(text) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.create_list(text) TO authenticated;

-- RPC: atomic per-item patch to avoid concurrent overwrites
CREATE OR REPLACE FUNCTION public.is_list_member(
  p_list_id UUID,
  p_user_id UUID
)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.list_members
    WHERE list_id = p_list_id
      AND user_id = p_user_id
  );
$$ LANGUAGE sql STABLE SECURITY DEFINER;
ALTER FUNCTION public.is_list_member(uuid, uuid) SET search_path = public;

CREATE OR REPLACE FUNCTION public.is_list_owner(
  p_list_id UUID,
  p_user_id UUID
)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.list_members
    WHERE list_id = p_list_id
      AND user_id = p_user_id
      AND role = 'owner'
  );
$$ LANGUAGE sql STABLE SECURITY DEFINER;
ALTER FUNCTION public.is_list_owner(uuid, uuid) SET search_path = public;

REVOKE EXECUTE ON FUNCTION public.is_list_member(uuid, uuid) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.is_list_member(uuid, uuid) TO authenticated;

REVOKE EXECUTE ON FUNCTION public.is_list_owner(uuid, uuid) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.is_list_owner(uuid, uuid) TO authenticated;

CREATE OR REPLACE FUNCTION update_list_item(
  p_list_id UUID,
  p_item_id TEXT,
  p_patch   JSONB
)
RETURNS VOID AS $$
BEGIN
  UPDATE shopping_lists
  SET items = (
    SELECT jsonb_agg(
      CASE WHEN item->>'id' = p_item_id
        THEN item || p_patch
        ELSE item
      END
    )
    FROM jsonb_array_elements(items) AS item
  ),
  updated_at = now()
  WHERE id = p_list_id
    AND public.is_list_member(p_list_id, auth.uid());
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;

REVOKE EXECUTE ON FUNCTION public.update_list_item(uuid, text, jsonb) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.update_list_item(uuid, text, jsonb) TO authenticated;

-- RLS: shopping_lists
ALTER TABLE shopping_lists ENABLE ROW LEVEL SECURITY;

CREATE POLICY "lists_select"
  ON shopping_lists FOR SELECT
  TO authenticated
  USING (
    user_id = auth.uid()
    OR public.is_list_member(id, auth.uid())
  );

CREATE POLICY "lists_insert"
  ON shopping_lists FOR INSERT
  TO authenticated
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "lists_update"
  ON shopping_lists FOR UPDATE
  TO authenticated
  USING (public.is_list_member(id, auth.uid()));

CREATE POLICY "lists_delete"
  ON shopping_lists FOR DELETE
  TO authenticated
  USING (user_id = auth.uid());

-- RLS: list_members
ALTER TABLE list_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY "list_members_select"
  ON list_members FOR SELECT
  TO authenticated
  USING (public.is_list_member(list_members.list_id, auth.uid()));

CREATE POLICY "list_members_insert_owner"
  ON list_members FOR INSERT
  TO authenticated
  WITH CHECK (
    public.is_list_owner(list_members.list_id, auth.uid())
    OR (user_id = auth.uid() AND role = 'owner')
  );

CREATE POLICY "list_members_delete_owner"
  ON list_members FOR DELETE
  TO authenticated
  USING (public.is_list_owner(list_members.list_id, auth.uid()));

-- RLS: list_invites (only service_role + authenticated owners)
ALTER TABLE list_invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "list_invites_select"
  ON list_invites FOR SELECT
  TO authenticated
  USING (invited_by = auth.uid());


-- Source: supabase/migrations/005_create_favorites_and_profiles.sql

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


-- Source: supabase/migrations/006_create_offers.sql

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


-- Source: supabase/migrations/007_storage_buckets.sql

-- Migration: storage_buckets
--
-- Crea i bucket Supabase Storage e le policy RLS su storage.objects.
-- Il service_role bypassa sempre RLS → non serve policy esplicita per i write
-- da FastAPI. Le policy qui controllano solo anon e authenticated.
--
-- Convenzione percorsi file:
--   flyers/          {user_id}/{flyer_id}.{ext}
--   avatars/         {user_id}.{ext}
--   logos/           {supermarket_slug}.png
--   product-images/  {product_id}.{ext}

-- Current Supabase self-hosted schema exposes only id/name/owner/timestamps on
-- storage.buckets. Public/private access comes from storage.objects RLS below.
INSERT INTO storage.buckets (id, name, public)
VALUES
  ('flyers',          'flyers',          false),
  ('avatars',         'avatars',         true),
  ('logos',           'logos',           true),
  ('product-images',  'product-images',  true)
ON CONFLICT (id) DO NOTHING;

-- ── Policy: bucket flyers (privato) ──────────────────────────────────────────
-- Upload: solo service_role (FastAPI) → bypassa RLS, nessuna policy necessaria
-- Download: solo il proprietario del file (path inizia con il proprio user_id)
-- Delete: solo service_role

CREATE POLICY "flyers_read_owner"
  ON storage.objects FOR SELECT
  TO authenticated
  USING (
    bucket_id = 'flyers'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

-- anon: nessun accesso al bucket flyers (deny by default con RLS abilitato)

-- ── Policy: bucket avatars (pubblico in lettura) ──────────────────────────────
-- Lettura pubblica gestita via policy esplicita.
CREATE POLICY "avatars_read_public"
  ON storage.objects FOR SELECT
  TO anon, authenticated
  USING (bucket_id = 'avatars');

-- ── Policy: bucket logos (pubblico in lettura) ────────────────────────────────
CREATE POLICY "logos_read_public"
  ON storage.objects FOR SELECT
  TO anon, authenticated
  USING (bucket_id = 'logos');

-- ── Policy: bucket product-images (pubblico in lettura) ──────────────────────
CREATE POLICY "product_images_read_public"
  ON storage.objects FOR SELECT
  TO anon, authenticated
  USING (bucket_id = 'product-images');


-- Source: supabase/migrations/008_add_file_hash_to_flyers.sql

-- Migration: add file_hash column to flyers

ALTER TABLE flyers ADD COLUMN IF NOT EXISTS file_hash TEXT;

-- Source: supabase/migrations/20260415075251_analytics_schema.sql

CREATE TABLE public.analytics_data (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    week_start date NOT NULL,
    metric_type text NOT NULL,
    category text,
    supermarket_id uuid,
    value double precision,
    description text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT analytics_data_pkey PRIMARY KEY (id)
);

ALTER TABLE public.analytics_data ADD CONSTRAINT analytics_data_supermarket_id_fkey FOREIGN KEY (supermarket_id) REFERENCES public.supermarkets(id);

CREATE INDEX idx_analytics_data_week_metric ON public.analytics_data (week_start, metric_type);

-- Source: supabase/migrations/20260416000000_extraction_log.sql

-- extraction_log: structured log of AI extraction pipeline events.
-- Used for debugging failed or low-quality flyer extractions.

CREATE TABLE public.extraction_log (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    flyer_id uuid,
    supermarket_id uuid,
    supermarket_name text,
    -- 'success' | 'error' | 'warning' | 'info'
    event_type text NOT NULL,
    message text NOT NULL,
    -- Extra context: page index, retry count, elapsed seconds, raw error, etc.
    details jsonb,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT extraction_log_pkey PRIMARY KEY (id),
    CONSTRAINT extraction_log_flyer_id_fkey
        FOREIGN KEY (flyer_id) REFERENCES public.flyers(id) ON DELETE SET NULL
);

CREATE INDEX idx_extraction_log_flyer_id   ON public.extraction_log (flyer_id);
CREATE INDEX idx_extraction_log_event_type ON public.extraction_log (event_type);
CREATE INDEX idx_extraction_log_created_at ON public.extraction_log (created_at DESC);


-- Source: supabase/migrations/20260416010000_flyer_requests.sql

-- Migration: create flyer_requests table
-- Users (guest or authenticated) can submit requests for flyers not yet
-- covered by the app. The backend notifies the admin via email.

create table if not exists flyer_requests (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  city        text not null,
  supermarket text,               -- null = "all supermarkets in this city"
  flyer_url   text,               -- optional direct link to the flyer
  notes       text check (char_length(notes) <= 500),
  email       text,               -- optional user email for reply
  user_id     uuid references auth.users(id) on delete set null,
  status      text not null default 'pending'
                check (status in ('pending', 'reviewed', 'done'))
);

-- RLS: INSERT open to everyone (authenticated + anonymous); SELECT/UPDATE restricted to service_role
alter table flyer_requests enable row level security;

create policy "Anyone can insert flyer requests"
  on flyer_requests for insert
  with check (true);

-- No SELECT / UPDATE policy → only service_role (bypasses RLS) can read/update rows


-- Source: supabase/migrations/20260416020000_fix_handle_new_user_search_path.sql

-- Fix search_path for SECURITY DEFINER function handle_new_user
-- Without this, the function can't find 'user_profiles' when called from auth.users trigger
ALTER FUNCTION handle_new_user() SET search_path = public;


-- Source: supabase/migrations/20260418000000_purchase_history.sql

CREATE TABLE purchase_history (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  list_id          UUID REFERENCES shopping_lists(id) ON DELETE SET NULL,
  list_item_id     TEXT NOT NULL,
  item_name        TEXT NOT NULL,
  product_id       UUID REFERENCES products(id),
  offer_id         UUID REFERENCES offers(id) ON DELETE SET NULL,
  supermarket_id   UUID REFERENCES supermarkets(id),
  supermarket_name TEXT,
  price_paid       NUMERIC(8,2) NOT NULL,
  price_original   NUMERIC(8,2),
  discount_pct     INTEGER,
  savings          NUMERIC(8,2) GENERATED ALWAYS AS (
                     COALESCE(price_original, price_paid) - price_paid
                   ) STORED,
  purchased_at     TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE purchase_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own purchase history"
  ON purchase_history FOR ALL TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE INDEX purchase_history_user_date_idx
  ON purchase_history (user_id, purchased_at DESC);


-- Source: supabase/migrations/20260418100000_products_soft_delete.sql

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


-- Source: supabase/migrations/20260421000000_role_managed_supermarket.sql

ALTER TABLE public.user_profiles
  ADD COLUMN role TEXT NOT NULL DEFAULT 'customer'
    CHECK (role IN ('customer', 'supermarket_manager', 'admin'));

ALTER TABLE public.user_profiles
  ADD COLUMN managed_supermarket_id UUID
    REFERENCES public.supermarkets(id) ON DELETE SET NULL;

ALTER TABLE public.user_profiles
  ADD CONSTRAINT chk_manager_needs_supermarket CHECK (
    (role = 'supermarket_manager' AND managed_supermarket_id IS NOT NULL)
    OR (role != 'supermarket_manager')
  );

CREATE INDEX idx_user_profiles_role ON public.user_profiles(role);
CREATE INDEX idx_user_profiles_managed_supermarket
  ON public.user_profiles(managed_supermarket_id)
  WHERE managed_supermarket_id IS NOT NULL;


-- Source: supabase/migrations/20260421010000_offers_is_confirmed.sql

ALTER TABLE public.offers ADD COLUMN is_confirmed BOOLEAN NOT NULL DEFAULT false;
UPDATE public.offers SET is_confirmed = true;
CREATE INDEX idx_offers_flyer_confirmed ON public.offers(flyer_id, is_confirmed);


-- Source: supabase/migrations/20260421020000_rls_confirmed_offers.sql

DROP POLICY "offers_anon_read" ON public.offers;
DROP POLICY "offers_auth_read" ON public.offers;

-- anon: active + confirmed + public flyer
CREATE POLICY "offers_anon_read"
  ON public.offers FOR SELECT TO anon
  USING (
    is_active = true AND is_confirmed = true
    AND EXISTS (SELECT 1 FROM flyers f WHERE f.id = offers.flyer_id AND f.is_public = true)
  );

-- authenticated: active+confirmed OR own flyer
CREATE POLICY "offers_auth_read"
  ON public.offers FOR SELECT TO authenticated
  USING (
    (is_active = true AND is_confirmed = true)
    OR EXISTS (
      SELECT 1 FROM flyers f
      WHERE f.id = offers.flyer_id
        AND (f.is_public = true OR f.user_id = auth.uid())
    )
  );

-- Same fix for products_anon_read (depends on offers):
DROP POLICY "products_anon_read" ON public.products;
CREATE POLICY "products_anon_read"
  ON public.products FOR SELECT TO anon
  USING (
    EXISTS (
      SELECT 1 FROM offers o JOIN flyers f ON f.id = o.flyer_id
      WHERE o.product_id = products.id
        AND f.is_public = true AND o.is_active = true AND o.is_confirmed = true
    )
  );


-- Source: supabase/migrations/20260423090000_offers_unit_price_fields.sql

ALTER TABLE public.offers
  ADD COLUMN unit_price_value NUMERIC(8,2),
  ADD COLUMN unit_price_unit TEXT;

ALTER TABLE public.offers
  ADD CONSTRAINT offers_unit_price_unit_check
  CHECK (
    unit_price_unit IS NULL
    OR unit_price_unit IN ('kg', 'l', 'kg sgocc')
  );

CREATE INDEX idx_offers_unit_price_value
  ON public.offers(unit_price_unit, unit_price_value);


-- Source: supabase/migrations/20260424103000_enable_rls_on_flyers.sql

alter table public.flyers enable row level security;


-- Source: supabase/migrations/20260424113000_harden_security_lints.sql

-- Harden Supabase security lints and align legacy extraction log schema.

ALTER TABLE IF EXISTS public.analytics_data ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename = 'scraping_log'
  ) AND NOT EXISTS (
    SELECT 1
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename = 'extraction_log'
  ) THEN
    ALTER TABLE public.scraping_log RENAME TO extraction_log;
  END IF;
END $$;

ALTER TABLE IF EXISTS public.extraction_log ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema = 'public'
      AND table_name = 'extraction_log'
      AND constraint_name = 'scraping_log_pkey'
  ) THEN
    ALTER TABLE public.extraction_log
      RENAME CONSTRAINT scraping_log_pkey TO extraction_log_pkey;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE table_schema = 'public'
      AND table_name = 'extraction_log'
      AND constraint_name = 'scraping_log_flyer_id_fkey'
  ) THEN
    ALTER TABLE public.extraction_log
      RENAME CONSTRAINT scraping_log_flyer_id_fkey TO extraction_log_flyer_id_fkey;
  END IF;
END $$;

ALTER INDEX IF EXISTS public.idx_scraping_log_flyer_id
  RENAME TO idx_extraction_log_flyer_id;

ALTER INDEX IF EXISTS public.idx_scraping_log_event_type
  RENAME TO idx_extraction_log_event_type;

ALTER INDEX IF EXISTS public.idx_scraping_log_created_at
  RENAME TO idx_extraction_log_created_at;

DROP POLICY IF EXISTS "Anyone can insert flyer requests" ON public.flyer_requests;

ALTER FUNCTION public.products_update_tsv() SET search_path = public;
ALTER FUNCTION public.create_list(text) SET search_path = public;
ALTER FUNCTION public.update_list_item(uuid, text, jsonb) SET search_path = public;
ALTER FUNCTION public.offers_compute_fields() SET search_path = public;
ALTER FUNCTION public.set_updated_at() SET search_path = public;


-- Source: supabase/migrations/20260427090000_postgis_geolocation.sql

-- Migration: postgis_geolocation
-- Adds indexed geography columns for distance queries.

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;

SET search_path = public, extensions;

ALTER TABLE public.supermarkets
  ADD COLUMN IF NOT EXISTS location extensions.geography(Point, 4326);

ALTER TABLE public.user_profiles
  ADD COLUMN IF NOT EXISTS home_location extensions.geography(Point, 4326),
  ADD COLUMN IF NOT EXISTS search_location extensions.geography(Point, 4326);

CREATE OR REPLACE FUNCTION public.set_supermarket_location_from_lat_lng()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
  IF NEW.lat IS NULL OR NEW.lng IS NULL THEN
    NEW.location := NULL;
  ELSE
    NEW.location := ST_SetSRID(ST_MakePoint(NEW.lng::double precision, NEW.lat::double precision), 4326)::geography;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_profile_locations_from_lat_lng()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
BEGIN
  IF NEW.home_lat IS NULL OR NEW.home_lng IS NULL THEN
    NEW.home_location := NULL;
  ELSE
    NEW.home_location := ST_SetSRID(ST_MakePoint(NEW.home_lng::double precision, NEW.home_lat::double precision), 4326)::geography;
  END IF;

  IF NEW.search_lat IS NULL OR NEW.search_lng IS NULL THEN
    NEW.search_location := NULL;
  ELSE
    NEW.search_location := ST_SetSRID(ST_MakePoint(NEW.search_lng::double precision, NEW.search_lat::double precision), 4326)::geography;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS supermarkets_set_location ON public.supermarkets;
CREATE TRIGGER supermarkets_set_location
  BEFORE INSERT OR UPDATE OF lat, lng ON public.supermarkets
  FOR EACH ROW EXECUTE FUNCTION public.set_supermarket_location_from_lat_lng();

DROP TRIGGER IF EXISTS user_profiles_set_locations ON public.user_profiles;
CREATE TRIGGER user_profiles_set_locations
  BEFORE INSERT OR UPDATE OF home_lat, home_lng, search_lat, search_lng ON public.user_profiles
  FOR EACH ROW EXECUTE FUNCTION public.set_profile_locations_from_lat_lng();

UPDATE public.supermarkets
SET location = ST_SetSRID(ST_MakePoint(lng::double precision, lat::double precision), 4326)::geography
WHERE lat IS NOT NULL
  AND lng IS NOT NULL;

UPDATE public.user_profiles
SET home_location = ST_SetSRID(ST_MakePoint(home_lng::double precision, home_lat::double precision), 4326)::geography
WHERE home_lat IS NOT NULL
  AND home_lng IS NOT NULL;

UPDATE public.user_profiles
SET search_location = ST_SetSRID(ST_MakePoint(search_lng::double precision, search_lat::double precision), 4326)::geography
WHERE search_lat IS NOT NULL
  AND search_lng IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_supermarkets_location
  ON public.supermarkets USING gist (location);

CREATE INDEX IF NOT EXISTS idx_user_profiles_home_location
  ON public.user_profiles USING gist (home_location);

CREATE INDEX IF NOT EXISTS idx_user_profiles_search_location
  ON public.user_profiles USING gist (search_location);

CREATE OR REPLACE FUNCTION public.nearby_supermarkets(
  user_lat double precision,
  user_lng double precision,
  radius_m double precision DEFAULT 10000
)
RETURNS TABLE(id uuid, distance_km double precision)
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
  WITH user_point AS (
    SELECT ST_SetSRID(ST_MakePoint(user_lng, user_lat), 4326)::geography AS location
  )
  SELECT
    sm.id,
    ST_Distance(sm.location, user_point.location) / 1000 AS distance_km
  FROM public.supermarkets AS sm
  CROSS JOIN user_point
  WHERE sm.is_active = true
    AND sm.location IS NOT NULL
    AND radius_m > 0
    AND ST_DWithin(sm.location, user_point.location, radius_m)
  ORDER BY distance_km ASC, sm.name ASC;
$$;

GRANT EXECUTE ON FUNCTION public.nearby_supermarkets(double precision, double precision, double precision)
  TO anon, authenticated, service_role;

RESET search_path;


-- Source: supabase/migrations/20260427100000_products_citext.sql

-- Migration: products_citext
-- Makes name/brand/format comparisons case-insensitive so UNIQUE constraint
-- deduplicates regardless of AI extraction casing ("barilla" = "Barilla" = "BARILLA").

CREATE EXTENSION IF NOT EXISTS citext;

ALTER TABLE products
  ALTER COLUMN name   TYPE citext USING name::citext,
  ALTER COLUMN brand  TYPE citext USING brand::citext;


-- Source: supabase/migrations/20260429010000_flyer_requests_add_supermarket_id.sql

-- Migration: add supermarket_id FK to flyer_requests
-- Enables counting requests per supermarket branch without string grouping.

alter table flyer_requests
  add column supermarket_id uuid references supermarkets(id) on delete set null;


-- Source: supabase/migrations/20260429020000_flyer_requests_drop_supermarket_id.sql

alter table flyer_requests
  drop column if exists supermarket_id;


-- Source: supabase/migrations/20260429030000_set_public_buckets.sql

-- avatars, logos, product-images buckets must have public=true so that
-- /storage/v1/object/public/{bucket}/{path} serves files without auth.
-- The buckets were created in 007_storage_buckets.sql without this flag.
UPDATE storage.buckets
SET public = true
WHERE id IN ('avatars', 'logos', 'product-images');


-- Source: supabase/migrations/20260429040000_purchase_history_drop_product_fk.sql

alter table purchase_history
drop constraint if exists purchase_history_product_id_fkey;


-- Source: supabase/migrations/20260502000000_push_subscriptions.sql

create table if not exists public.push_subscriptions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  endpoint    text not null,
  p256dh      text not null,
  auth_key    text not null,
  user_agent  text,
  created_at  timestamptz not null default now(),
  unique (user_id, endpoint)
);

alter table public.push_subscriptions enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename  = 'push_subscriptions'
      and policyname = 'push_subscriptions_self_manage'
  ) then
    create policy "push_subscriptions_self_manage"
      on public.push_subscriptions
      for all
      using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
  end if;
end $$;


-- Source: supabase/migrations/20260503111000_offers_active_window.sql

CREATE OR REPLACE FUNCTION public.offer_is_currently_active(
  p_valid_from DATE,
  p_valid_to DATE
)
RETURNS BOOLEAN AS $$
  SELECT
    (p_valid_from IS NULL OR p_valid_from <= CURRENT_DATE)
    AND
    (p_valid_to IS NULL OR p_valid_to >= CURRENT_DATE);
$$ LANGUAGE sql STABLE;


CREATE OR REPLACE FUNCTION public.offers_compute_fields()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.price_original IS NOT NULL AND NEW.price_original > 0 THEN
    NEW.discount_pct := ROUND(
      ((NEW.price_original - NEW.price_offer) / NEW.price_original) * 100
    );
  ELSE
    NEW.discount_pct := NULL;
  END IF;

  NEW.is_active := public.offer_is_currently_active(NEW.valid_from, NEW.valid_to);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;


UPDATE public.offers
SET
  valid_from = valid_from,
  valid_to = valid_to;


DROP POLICY IF EXISTS "offers_anon_read" ON public.offers;
CREATE POLICY "offers_anon_read"
  ON public.offers FOR SELECT TO anon
  USING (
    public.offer_is_currently_active(valid_from, valid_to)
    AND is_confirmed = true
    AND EXISTS (
      SELECT 1
      FROM public.flyers f
      WHERE f.id = offers.flyer_id
        AND f.is_public = true
    )
  );


DROP POLICY IF EXISTS "offers_auth_read" ON public.offers;
CREATE POLICY "offers_auth_read"
  ON public.offers FOR SELECT TO authenticated
  USING (
    (
      public.offer_is_currently_active(valid_from, valid_to)
      AND is_confirmed = true
    )
    OR EXISTS (
      SELECT 1
      FROM public.flyers f
      WHERE f.id = offers.flyer_id
        AND (f.is_public = true OR f.user_id = auth.uid())
    )
  );


DROP POLICY IF EXISTS "products_anon_read" ON public.products;
CREATE POLICY "products_anon_read"
  ON public.products FOR SELECT TO anon
  USING (
    EXISTS (
      SELECT 1
      FROM public.offers o
      JOIN public.flyers f ON f.id = o.flyer_id
      WHERE o.product_id = products.id
        AND f.is_public = true
        AND o.is_confirmed = true
        AND public.offer_is_currently_active(o.valid_from, o.valid_to)
    )
  );


-- Source: supabase/migrations/20260504000000_search_products_catalog.sql

-- pg_trgm extension for fuzzy product catalog search.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS products_name_trgm
  ON products USING GIN (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS products_brand_trgm
  ON products USING GIN (brand gin_trgm_ops);

CREATE OR REPLACE FUNCTION search_products_catalog(query text, lim integer DEFAULT 10)
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


-- Source: supabase/migrations/20260504010000_default_list_on_user_signup.sql

-- Ensure each new auth user starts with a profile and an empty owned list.

CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
  v_list_id UUID;
BEGIN
  INSERT INTO user_profiles (id, display_name)
  VALUES (
    NEW.id,
    NEW.raw_user_meta_data->>'display_name'
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT shopping_lists.id
  INTO v_list_id
  FROM shopping_lists
  WHERE shopping_lists.user_id = NEW.id
    AND shopping_lists.is_active = true
  LIMIT 1;

  IF v_list_id IS NULL THEN
    INSERT INTO shopping_lists (user_id, name, items, is_active)
    VALUES (NEW.id, 'Lista spesa', '[]'::jsonb, true)
    RETURNING id INTO v_list_id;
  END IF;

  INSERT INTO list_members (list_id, user_id, role)
  VALUES (v_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


-- Source: supabase/migrations/20260508104500_purchase_history_quantity.sql

alter table purchase_history
add column if not exists quantity numeric(8,2) not null default 1;


-- Source: supabase/migrations/20260508113000_security_lints_cleanup.sql

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

ALTER FUNCTION public.create_list(text) SECURITY INVOKER;
REVOKE EXECUTE ON FUNCTION public.create_list(text) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.create_list(text) TO authenticated;

ALTER FUNCTION public.update_list_item(uuid, text, jsonb) SECURITY INVOKER;
REVOKE EXECUTE ON FUNCTION public.update_list_item(uuid, text, jsonb) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.update_list_item(uuid, text, jsonb) TO authenticated;


-- Source: supabase/migrations/20260508153000_search_products_catalog_prefix_support.sql

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


-- Source: supabase/migrations/20260508181500_internal_tables_explicit_deny_policies.sql

-- Add explicit deny-all RLS policies for internal-only tables.
-- Service role still bypasses RLS; anon/authenticated clients stay blocked.

DROP POLICY IF EXISTS "Anyone can insert flyer requests" ON public.flyer_requests;

DROP POLICY IF EXISTS analytics_data_deny_all ON public.analytics_data;
CREATE POLICY analytics_data_deny_all
  ON public.analytics_data
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);

DROP POLICY IF EXISTS extraction_log_deny_all ON public.extraction_log;
CREATE POLICY extraction_log_deny_all
  ON public.extraction_log
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);

DROP POLICY IF EXISTS flyer_requests_deny_all ON public.flyer_requests;
CREATE POLICY flyer_requests_deny_all
  ON public.flyer_requests
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);


-- Source: supabase/migrations/20260509120000_multi_list_sharing_notifications.sql

ALTER TABLE public.shopping_lists
  ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT false;

WITH ranked_lists AS (
  SELECT
    id,
    user_id,
    ROW_NUMBER() OVER (
      PARTITION BY user_id
      ORDER BY is_active DESC NULLS LAST, created_at ASC NULLS LAST, id ASC
    ) AS row_num
  FROM public.shopping_lists
  WHERE user_id IS NOT NULL
)
UPDATE public.shopping_lists AS shopping_lists
SET is_default = ranked_lists.row_num = 1
FROM ranked_lists
WHERE ranked_lists.id = shopping_lists.id;

CREATE UNIQUE INDEX IF NOT EXISTS idx_shopping_lists_one_default_per_user
  ON public.shopping_lists(user_id)
  WHERE is_default = true AND user_id IS NOT NULL;

ALTER TABLE public.user_profiles
  ADD COLUMN IF NOT EXISTS active_list_id UUID REFERENCES public.shopping_lists(id) ON DELETE SET NULL;

UPDATE public.user_profiles AS user_profiles
SET active_list_id = shopping_lists.id
FROM public.shopping_lists
WHERE shopping_lists.user_id = user_profiles.id
  AND shopping_lists.is_default = true
  AND user_profiles.active_list_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_user_profiles_active_list_id
  ON public.user_profiles(active_list_id);

ALTER TABLE public.list_invites
  ADD COLUMN IF NOT EXISTS invited_user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS declined_at TIMESTAMPTZ;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE constraint_schema = 'public'
      AND table_name = 'list_invites'
      AND constraint_name = 'list_invites_status_check'
  ) THEN
    ALTER TABLE public.list_invites DROP CONSTRAINT list_invites_status_check;
  END IF;
END $$;

ALTER TABLE public.list_invites
  ADD CONSTRAINT list_invites_status_check
  CHECK (status IN ('pending', 'accepted', 'declined', 'expired', 'revoked'));

CREATE UNIQUE INDEX IF NOT EXISTS idx_list_invites_pending_target
  ON public.list_invites(list_id, invited_user_id)
  WHERE status = 'pending' AND invited_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.app_notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  read_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_notifications_user_created_at
  ON public.app_notifications(user_id, created_at DESC);

ALTER TABLE public.app_notifications ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'app_notifications'
      AND policyname = 'app_notifications_select_self'
  ) THEN
    CREATE POLICY "app_notifications_select_self"
      ON public.app_notifications
      FOR SELECT
      TO authenticated
      USING (user_id = auth.uid());
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'app_notifications'
      AND policyname = 'app_notifications_update_self'
  ) THEN
    CREATE POLICY "app_notifications_update_self"
      ON public.app_notifications
      FOR UPDATE
      TO authenticated
      USING (user_id = auth.uid())
      WITH CHECK (user_id = auth.uid());
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.create_list(p_name TEXT)
RETURNS UUID AS $$
DECLARE
  v_list_id UUID;
BEGIN
  INSERT INTO public.shopping_lists (user_id, name, is_default)
  VALUES (auth.uid(), p_name, false)
  RETURNING id INTO v_list_id;

  INSERT INTO public.list_members (list_id, user_id, role)
  VALUES (v_list_id, auth.uid(), 'owner');

  RETURN v_list_id;
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;
ALTER FUNCTION public.create_list(text) SET search_path = public;

CREATE OR REPLACE FUNCTION public.append_list_item(
  p_list_id UUID,
  p_item JSONB
)
RETURNS VOID AS $$
BEGIN
  UPDATE public.shopping_lists
  SET items = COALESCE(items, '[]'::jsonb) || jsonb_build_array(p_item),
      updated_at = now()
  WHERE id = p_list_id
    AND EXISTS (
      SELECT 1
      FROM public.list_members lm
      WHERE lm.list_id = p_list_id
        AND lm.user_id = auth.uid()
    );
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;
ALTER FUNCTION public.append_list_item(uuid, jsonb) SET search_path = public;

CREATE OR REPLACE FUNCTION public.remove_list_item(
  p_list_id UUID,
  p_item_id TEXT
)
RETURNS VOID AS $$
BEGIN
  UPDATE public.shopping_lists
  SET items = COALESCE(
        (
          SELECT jsonb_agg(item)
          FROM jsonb_array_elements(items) AS item
          WHERE item->>'id' <> p_item_id
        ),
        '[]'::jsonb
      ),
      updated_at = now()
  WHERE id = p_list_id
    AND EXISTS (
      SELECT 1
      FROM public.list_members lm
      WHERE lm.list_id = p_list_id
        AND lm.user_id = auth.uid()
    );
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;
ALTER FUNCTION public.remove_list_item(uuid, text) SET search_path = public;

REVOKE EXECUTE ON FUNCTION public.append_list_item(uuid, jsonb) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.append_list_item(uuid, jsonb) TO authenticated;

REVOKE EXECUTE ON FUNCTION public.remove_list_item(uuid, text) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.remove_list_item(uuid, text) TO authenticated;

DROP POLICY IF EXISTS "lists_delete" ON public.shopping_lists;

CREATE POLICY "lists_delete"
  ON public.shopping_lists
  FOR DELETE
  TO authenticated
  USING (user_id = auth.uid() AND is_default = false);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'list_invites'
      AND policyname = 'list_invites_select'
  ) THEN
    DROP POLICY "list_invites_select" ON public.list_invites;
  END IF;
END $$;

CREATE POLICY "list_invites_select"
  ON public.list_invites
  FOR SELECT
  TO authenticated
  USING (
    invited_by = auth.uid()
    OR invited_user_id = auth.uid()
  );

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
  v_list_id UUID;
BEGIN
  INSERT INTO public.user_profiles (id, display_name)
  VALUES (
    NEW.id,
    NEW.raw_user_meta_data->>'display_name'
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT shopping_lists.id
  INTO v_list_id
  FROM public.shopping_lists
  WHERE shopping_lists.user_id = NEW.id
    AND shopping_lists.is_default = true
  LIMIT 1;

  IF v_list_id IS NULL THEN
    INSERT INTO public.shopping_lists (user_id, name, items, is_active, is_default)
    VALUES (NEW.id, 'Lista spesa', '[]'::jsonb, true, true)
    RETURNING id INTO v_list_id;
  END IF;

  INSERT INTO public.list_members (list_id, user_id, role)
  VALUES (v_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  UPDATE public.user_profiles
  SET active_list_id = COALESCE(active_list_id, v_list_id)
  WHERE id = NEW.id;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;


-- Source: supabase/migrations/20260511110000_default_list_name_guard.sql

ALTER TABLE public.shopping_lists
  ALTER COLUMN name SET DEFAULT 'Lista principale';

UPDATE public.shopping_lists
SET name = 'Lista principale'
WHERE is_default = true;

CREATE OR REPLACE FUNCTION public.prevent_default_list_rename()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.is_default = true AND NEW.name IS DISTINCT FROM OLD.name THEN
    RAISE EXCEPTION 'Default list cannot be renamed';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS prevent_default_list_rename_trigger
  ON public.shopping_lists;

CREATE TRIGGER prevent_default_list_rename_trigger
BEFORE UPDATE ON public.shopping_lists
FOR EACH ROW
WHEN (OLD.is_default = true)
EXECUTE FUNCTION public.prevent_default_list_rename();

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  created_list_id UUID;
BEGIN
  INSERT INTO public.user_profiles (
    id,
    display_name,
    home_address,
    home_city,
    home_province,
    home_postal_code,
    role,
    managed_supermarket_id,
    active_list_id
  )
  VALUES (
    NEW.id,
    COALESCE(
      NEW.raw_user_meta_data->>'display_name',
      split_part(NEW.email, '@', 1)
    ),
    '',
    '',
    '',
    '',
    'customer',
    NULL,
    NULL
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT shopping_lists.id
    INTO created_list_id
  FROM public.shopping_lists
  WHERE shopping_lists.user_id = NEW.id
    AND shopping_lists.is_default = true
  ORDER BY shopping_lists.created_at ASC NULLS LAST, shopping_lists.id ASC
  LIMIT 1;

  IF created_list_id IS NULL THEN
    INSERT INTO public.shopping_lists (user_id, name, items, is_active, is_default)
    VALUES (NEW.id, 'Lista principale', '[]'::jsonb, true, true)
    RETURNING id INTO created_list_id;
  END IF;

  INSERT INTO public.list_members (list_id, user_id, role)
  VALUES (created_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  UPDATE public.user_profiles
  SET active_list_id = created_list_id
  WHERE id = NEW.id
    AND active_list_id IS NULL;

  RETURN NEW;
END;
$$;


-- Source: supabase/migrations/20260511123000_prevent_default_list_rename_search_path.sql

ALTER FUNCTION public.prevent_default_list_rename()
SET search_path = public;


-- Source: supabase/migrations/20260513000000_fix_list_members_rls_recursion.sql

-- Fix infinite recursion in list_members RLS policies.
-- The policies were using inline self-referential subqueries; restore SECURITY DEFINER
-- helper functions so the membership check bypasses RLS when called from a policy.

CREATE OR REPLACE FUNCTION public.is_list_member(
  p_list_id UUID,
  p_user_id UUID
)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.list_members
    WHERE list_id = p_list_id
      AND user_id = p_user_id
  );
$$ LANGUAGE sql STABLE SECURITY DEFINER;
ALTER FUNCTION public.is_list_member(uuid, uuid) SET search_path = public;

CREATE OR REPLACE FUNCTION public.is_list_owner(
  p_list_id UUID,
  p_user_id UUID
)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.list_members
    WHERE list_id = p_list_id
      AND user_id = p_user_id
      AND role = 'owner'
  );
$$ LANGUAGE sql STABLE SECURITY DEFINER;
ALTER FUNCTION public.is_list_owner(uuid, uuid) SET search_path = public;

REVOKE EXECUTE ON FUNCTION public.is_list_member(uuid, uuid) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.is_list_member(uuid, uuid) TO authenticated;

REVOKE EXECUTE ON FUNCTION public.is_list_owner(uuid, uuid) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.is_list_owner(uuid, uuid) TO authenticated;

-- Drop and recreate policies using the SECURITY DEFINER helpers (no self-reference).
DROP POLICY IF EXISTS "list_members_select" ON public.list_members;
CREATE POLICY "list_members_select"
  ON public.list_members FOR SELECT
  TO authenticated
  USING (public.is_list_member(list_members.list_id, auth.uid()));

DROP POLICY IF EXISTS "list_members_insert_owner" ON public.list_members;
CREATE POLICY "list_members_insert_owner"
  ON public.list_members FOR INSERT
  TO authenticated
  WITH CHECK (
    public.is_list_owner(list_members.list_id, auth.uid())
    OR (user_id = auth.uid() AND role = 'owner')
  );

DROP POLICY IF EXISTS "list_members_delete_owner" ON public.list_members;
CREATE POLICY "list_members_delete_owner"
  ON public.list_members FOR DELETE
  TO authenticated
  USING (public.is_list_owner(list_members.list_id, auth.uid()));


-- Source: supabase/migrations/20260513200000_format_to_offers.sql

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


-- Source: supabase/migrations/20260514010000_offers_is_reviewed.sql

ALTER TABLE public.offers
  ADD COLUMN is_reviewed BOOLEAN NOT NULL DEFAULT false;


-- Source: supabase/migrations/20260514020000_offers_flyer_cascade.sql

-- Change offers.flyer_id FK from ON DELETE SET NULL to ON DELETE CASCADE
-- Deleting a flyer now hard-deletes all linked offers (draft and confirmed).
ALTER TABLE offers
  DROP CONSTRAINT IF EXISTS offers_flyer_id_fkey;

ALTER TABLE offers
  ADD CONSTRAINT offers_flyer_id_fkey
    FOREIGN KEY (flyer_id)
    REFERENCES flyers(id)
    ON DELETE CASCADE;


-- Source: supabase/migrations/20260515120602_persist_signup_address_metadata.sql

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  created_list_id UUID;
BEGIN
  INSERT INTO public.user_profiles (
    id,
    display_name,
    home_address,
    home_city,
    home_province,
    home_postal_code,
    role,
    managed_supermarket_id,
    active_list_id
  )
  VALUES (
    NEW.id,
    COALESCE(
      NEW.raw_user_meta_data->>'display_name',
      split_part(NEW.email, '@', 1)
    ),
    COALESCE(NEW.raw_user_meta_data->>'home_address', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_city', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_province', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_postal_code', ''),
    'customer',
    NULL,
    NULL
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT shopping_lists.id
    INTO created_list_id
  FROM public.shopping_lists
  WHERE shopping_lists.user_id = NEW.id
    AND shopping_lists.is_default = true
  ORDER BY shopping_lists.created_at ASC NULLS LAST, shopping_lists.id ASC
  LIMIT 1;

  IF created_list_id IS NULL THEN
    INSERT INTO public.shopping_lists (user_id, name, items, is_active, is_default)
    VALUES (NEW.id, 'Lista principale', '[]'::jsonb, true, true)
    RETURNING id INTO created_list_id;
  END IF;

  INSERT INTO public.list_members (list_id, user_id, role)
  VALUES (created_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  UPDATE public.user_profiles
  SET active_list_id = created_list_id
  WHERE id = NEW.id
    AND active_list_id IS NULL;

  RETURN NEW;
END;
$$;

ALTER FUNCTION public.products_update_tsv() SET search_path = public;


-- Source: supabase/migrations/20260515133802_offers_full_unique_conflict_index.sql

DROP INDEX IF EXISTS public.idx_offers_product_flyer_format;

CREATE UNIQUE INDEX idx_offers_product_flyer_format
  ON public.offers(product_id, flyer_id, format_key);


-- Source: supabase/migrations/20260521143000_deferred_draft_product_binding.sql

-- Draft offers can be reviewed before creating a new canonical product.
-- Existing-product bindings remain visible; unbound drafts create products only on confirmation.

ALTER TABLE public.offers
  ADD COLUMN draft_name TEXT,
  ADD COLUMN draft_brand TEXT,
  ADD COLUMN draft_category TEXT,
  ADD COLUMN draft_subcategory TEXT,
  ADD COLUMN draft_product_key TEXT;

UPDATE public.offers o
SET
  draft_name = p.name,
  draft_brand = p.brand,
  draft_category = p.category,
  draft_subcategory = p.subcategory,
  draft_product_key = lower(trim(coalesce(p.name, ''))) || '|' || lower(trim(coalesce(p.brand, '')))
FROM public.products p
WHERE o.product_id = p.id;

ALTER TABLE public.offers
  ALTER COLUMN product_id DROP NOT NULL;

ALTER TABLE public.offers
  ADD CONSTRAINT offers_confirmed_product_required
  CHECK (is_confirmed = false OR product_id IS NOT NULL);

DROP INDEX IF EXISTS public.idx_offers_product_flyer_format;

CREATE UNIQUE INDEX idx_offers_flyer_draft_product_format
  ON public.offers(flyer_id, draft_product_key, format_key);


-- Source: supabase/migrations/20260525110000_purchase_history_snapshot_fields.sql

alter table purchase_history
add column if not exists brand text,
add column if not exists format_label text,
add column if not exists image_url text,
add column if not exists category text,
add column if not exists subcategory text,
add column if not exists unit_price text,
add column if not exists unit_price_value numeric(10,2),
add column if not exists unit_price_unit text,
add column if not exists unit_price_label text;


-- Source: supabase/migrations/20260526073230_draft_offer_image_url.sql

ALTER TABLE public.offers
ADD COLUMN IF NOT EXISTS draft_image_url TEXT;


-- Source: supabase/migrations/20260528071646_move_list_rls_helpers_to_private_schema.sql

-- Move RLS-only list helper functions out of the exposed public API schema.
-- These helpers must remain SECURITY DEFINER to avoid recursive RLS checks,
-- but they should not be callable via PostgREST RPC endpoints.

CREATE SCHEMA IF NOT EXISTS private;

REVOKE ALL ON SCHEMA private FROM PUBLIC;
REVOKE ALL ON SCHEMA private FROM anon;
GRANT USAGE ON SCHEMA private TO authenticated;

CREATE OR REPLACE FUNCTION private.is_list_member(
  p_list_id UUID,
  p_user_id UUID
)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.list_members
    WHERE list_id = p_list_id
      AND user_id = p_user_id
  );
$$ LANGUAGE sql STABLE SECURITY DEFINER;
ALTER FUNCTION private.is_list_member(uuid, uuid) SET search_path = public, pg_temp;

CREATE OR REPLACE FUNCTION private.is_list_owner(
  p_list_id UUID,
  p_user_id UUID
)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.list_members
    WHERE list_id = p_list_id
      AND user_id = p_user_id
      AND role = 'owner'
  );
$$ LANGUAGE sql STABLE SECURITY DEFINER;
ALTER FUNCTION private.is_list_owner(uuid, uuid) SET search_path = public, pg_temp;

REVOKE EXECUTE ON FUNCTION private.is_list_member(uuid, uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION private.is_list_member(uuid, uuid) TO authenticated;

REVOKE EXECUTE ON FUNCTION private.is_list_owner(uuid, uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION private.is_list_owner(uuid, uuid) TO authenticated;

CREATE OR REPLACE FUNCTION public.update_list_item(
  p_list_id UUID,
  p_item_id TEXT,
  p_patch   JSONB
)
RETURNS VOID AS $$
BEGIN
  UPDATE public.shopping_lists
  SET items = (
    SELECT jsonb_agg(
      CASE WHEN item->>'id' = p_item_id
        THEN item || p_patch
        ELSE item
      END
    )
    FROM jsonb_array_elements(items) AS item
  ),
  updated_at = now()
  WHERE id = p_list_id
    AND private.is_list_member(p_list_id, auth.uid());
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;
ALTER FUNCTION public.update_list_item(uuid, text, jsonb) SET search_path = public;

DROP POLICY IF EXISTS "lists_select" ON public.shopping_lists;
CREATE POLICY "lists_select"
  ON public.shopping_lists FOR SELECT
  TO authenticated
  USING (
    user_id = auth.uid()
    OR private.is_list_member(id, auth.uid())
  );

DROP POLICY IF EXISTS "lists_update" ON public.shopping_lists;
CREATE POLICY "lists_update"
  ON public.shopping_lists FOR UPDATE
  TO authenticated
  USING (private.is_list_member(id, auth.uid()));

DROP POLICY IF EXISTS "list_members_select" ON public.list_members;
CREATE POLICY "list_members_select"
  ON public.list_members FOR SELECT
  TO authenticated
  USING (private.is_list_member(list_members.list_id, auth.uid()));

DROP POLICY IF EXISTS "list_members_insert_owner" ON public.list_members;
CREATE POLICY "list_members_insert_owner"
  ON public.list_members FOR INSERT
  TO authenticated
  WITH CHECK (
    private.is_list_owner(list_members.list_id, auth.uid())
    OR (user_id = auth.uid() AND role = 'owner')
  );

DROP POLICY IF EXISTS "list_members_delete_owner" ON public.list_members;
CREATE POLICY "list_members_delete_owner"
  ON public.list_members FOR DELETE
  TO authenticated
  USING (private.is_list_owner(list_members.list_id, auth.uid()));

DROP FUNCTION IF EXISTS public.is_list_member(uuid, uuid);
DROP FUNCTION IF EXISTS public.is_list_owner(uuid, uuid);


-- Source: supabase/migrations/20260601093000_account_delete_invite_fk_cleanup.sql

ALTER TABLE public.list_members
  DROP CONSTRAINT IF EXISTS list_members_invited_by_fkey;

ALTER TABLE public.list_members
  ADD CONSTRAINT list_members_invited_by_fkey
  FOREIGN KEY (invited_by)
  REFERENCES auth.users(id)
  ON DELETE SET NULL;

ALTER TABLE public.list_invites
  DROP CONSTRAINT IF EXISTS list_invites_invited_by_fkey;

ALTER TABLE public.list_invites
  ADD CONSTRAINT list_invites_invited_by_fkey
  FOREIGN KEY (invited_by)
  REFERENCES auth.users(id)
  ON DELETE CASCADE;

ALTER TABLE public.list_invites
  DROP CONSTRAINT IF EXISTS list_invites_accepted_by_fkey;

ALTER TABLE public.list_invites
  ADD CONSTRAINT list_invites_accepted_by_fkey
  FOREIGN KEY (accepted_by)
  REFERENCES auth.users(id)
  ON DELETE SET NULL;


-- Source: supabase/migrations/20260601103000_shared_list_notification_preference.sql

ALTER TABLE public.user_profiles
ADD COLUMN IF NOT EXISTS notification_shared_lists BOOLEAN DEFAULT true;

UPDATE public.user_profiles
SET notification_shared_lists = true
WHERE notification_shared_lists IS NULL;


-- Source: supabase/migrations/20260601111500_drop_notification_expiry_preference.sql

ALTER TABLE public.user_profiles
DROP COLUMN IF EXISTS notification_expiry;


-- Source: supabase/migrations/20260601123745_notifications_enabled_all_or_none.sql

SET lock_timeout = '5s';

ALTER TABLE public.user_profiles
  ADD COLUMN IF NOT EXISTS notifications_enabled BOOLEAN NOT NULL DEFAULT true;

UPDATE public.user_profiles
SET notifications_enabled =
  COALESCE(notification_deals, true)
  OR COALESCE(notification_favorites, true)
  OR COALESCE(notification_shared_lists, true);

ALTER TABLE public.user_profiles
  DROP COLUMN IF EXISTS notification_deals,
  DROP COLUMN IF EXISTS notification_favorites,
  DROP COLUMN IF EXISTS notification_shared_lists;


-- Source: supabase/migrations/20260603110000_multi_supermarket_flyers.sql

CREATE TABLE IF NOT EXISTS public.manager_supermarkets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  supermarket_id UUID NOT NULL REFERENCES public.supermarkets(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, supermarket_id)
);

CREATE INDEX IF NOT EXISTS idx_manager_supermarkets_user_id
  ON public.manager_supermarkets(user_id);

CREATE INDEX IF NOT EXISTS idx_manager_supermarkets_supermarket_id
  ON public.manager_supermarkets(supermarket_id);

INSERT INTO public.manager_supermarkets (user_id, supermarket_id)
SELECT id, managed_supermarket_id
FROM public.user_profiles
WHERE managed_supermarket_id IS NOT NULL
ON CONFLICT (user_id, supermarket_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.flyer_targets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  flyer_id UUID NOT NULL REFERENCES public.flyers(id) ON DELETE CASCADE,
  supermarket_id UUID NOT NULL REFERENCES public.supermarkets(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (flyer_id, supermarket_id)
);

CREATE INDEX IF NOT EXISTS idx_flyer_targets_flyer_id
  ON public.flyer_targets(flyer_id);

CREATE INDEX IF NOT EXISTS idx_flyer_targets_supermarket_id
  ON public.flyer_targets(supermarket_id);

ALTER TABLE public.flyers
  ADD COLUMN IF NOT EXISTS flyer_kind TEXT NOT NULL DEFAULT 'source'
    CHECK (flyer_kind IN ('source', 'published_target')),
  ADD COLUMN IF NOT EXISTS source_flyer_id UUID REFERENCES public.flyers(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_flyers_flyer_kind
  ON public.flyers(flyer_kind);

CREATE INDEX IF NOT EXISTS idx_flyers_source_flyer_id
  ON public.flyers(source_flyer_id)
  WHERE source_flyer_id IS NOT NULL;

UPDATE public.flyers
SET flyer_kind = CASE
  WHEN is_public = true THEN 'published_target'
  ELSE 'source'
END
WHERE flyer_kind IS DISTINCT FROM CASE
  WHEN is_public = true THEN 'published_target'
  ELSE 'source'
END;

INSERT INTO public.flyer_targets (flyer_id, supermarket_id)
SELECT id, supermarket_id
FROM public.flyers
WHERE flyer_kind = 'source'
  AND supermarket_id IS NOT NULL
ON CONFLICT (flyer_id, supermarket_id) DO NOTHING;


-- Source: supabase/migrations/20260605113000_offer_kind_source_links.sql

ALTER TABLE public.offers
  ADD COLUMN IF NOT EXISTS offer_kind TEXT NOT NULL DEFAULT 'source_master',
  ADD COLUMN IF NOT EXISTS source_offer_id UUID REFERENCES public.offers(id) ON DELETE CASCADE;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'offers_offer_kind_check'
  ) THEN
    ALTER TABLE public.offers
      ADD CONSTRAINT offers_offer_kind_check
      CHECK (offer_kind IN ('source_master', 'published_target'));
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_offers_offer_kind
  ON public.offers(offer_kind);

CREATE INDEX IF NOT EXISTS idx_offers_source_offer_id
  ON public.offers(source_offer_id)
  WHERE source_offer_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_offers_source_offer_target_unique
  ON public.offers(source_offer_id, supermarket_id)
  WHERE source_offer_id IS NOT NULL;

UPDATE public.offers o
SET offer_kind = CASE
  WHEN f.flyer_kind = 'published_target' THEN 'published_target'
  ELSE 'source_master'
END
FROM public.flyers f
WHERE f.id = o.flyer_id
  AND (
    o.offer_kind IS NULL
    OR o.offer_kind <> CASE
      WHEN f.flyer_kind = 'published_target' THEN 'published_target'
      ELSE 'source_master'
    END
  );

WITH published_rows AS (
  SELECT
    published.id AS published_offer_id,
    source.id AS source_offer_id
  FROM public.offers published
  JOIN public.flyers published_flyer
    ON published_flyer.id = published.flyer_id
   AND published_flyer.flyer_kind = 'published_target'
  LEFT JOIN LATERAL (
    SELECT source.id
    FROM public.offers source
    WHERE source.flyer_id = published_flyer.source_flyer_id
      AND source.offer_kind = 'source_master'
      AND source.product_id IS NOT DISTINCT FROM published.product_id
      AND source.draft_product_key IS NOT DISTINCT FROM published.draft_product_key
      AND source.format_key IS NOT DISTINCT FROM published.format_key
    ORDER BY source.created_at, source.id
    LIMIT 1
  ) source ON TRUE
)
UPDATE public.offers published
SET source_offer_id = published_rows.source_offer_id
FROM published_rows
WHERE published.id = published_rows.published_offer_id
  AND published.offer_kind = 'published_target'
  AND published_rows.source_offer_id IS NOT NULL
  AND published.source_offer_id IS DISTINCT FROM published_rows.source_offer_id;

DROP POLICY IF EXISTS "offers_anon_read" ON public.offers;
CREATE POLICY "offers_anon_read"
  ON public.offers FOR SELECT TO anon
  USING (
    public.offer_is_currently_active(valid_from, valid_to)
    AND is_confirmed = true
    AND offer_kind = 'published_target'
    AND EXISTS (
      SELECT 1
      FROM public.flyers f
      WHERE f.id = offers.flyer_id
        AND f.is_public = true
    )
  );

DROP POLICY IF EXISTS "offers_auth_read" ON public.offers;
CREATE POLICY "offers_auth_read"
  ON public.offers FOR SELECT TO authenticated
  USING (
    (
      public.offer_is_currently_active(valid_from, valid_to)
      AND is_confirmed = true
      AND offer_kind = 'published_target'
    )
    OR EXISTS (
      SELECT 1
      FROM public.flyers f
      WHERE f.id = offers.flyer_id
        AND (f.is_public = true OR f.user_id = auth.uid())
    )
  );

DROP POLICY IF EXISTS "products_anon_read" ON public.products;
CREATE POLICY "products_anon_read"
  ON public.products FOR SELECT TO anon
  USING (
    EXISTS (
      SELECT 1
      FROM public.offers o
      JOIN public.flyers f ON f.id = o.flyer_id
      WHERE o.product_id = products.id
        AND f.is_public = true
        AND o.is_confirmed = true
        AND o.offer_kind = 'published_target'
        AND public.offer_is_currently_active(o.valid_from, o.valid_to)
    )
  );


-- Source: supabase/migrations/20260605160000_single_shared_list_mvp.sql

CREATE OR REPLACE FUNCTION public.merge_shopping_list_items(
  base_items jsonb,
  incoming_items jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  item jsonb;
  existing jsonb;
  merged jsonb := COALESCE(base_items, '[]'::jsonb);
  match_index integer;
BEGIN
  FOR item IN
    SELECT value
    FROM jsonb_array_elements(COALESCE(incoming_items, '[]'::jsonb))
  LOOP
    match_index := NULL;

    SELECT value, ordinality - 1
    INTO existing, match_index
    FROM jsonb_array_elements(merged) WITH ORDINALITY
    WHERE (
      item->>'pinned_offer_id' IS NOT NULL
      AND value->>'pinned_offer_id' = item->>'pinned_offer_id'
    ) OR (
      item->>'pinned_offer_id' IS NULL
      AND item->>'pinned_product_id' IS NOT NULL
      AND value->>'pinned_product_id' = item->>'pinned_product_id'
    ) OR (
      item->>'pinned_offer_id' IS NULL
      AND item->>'pinned_product_id' IS NULL
      AND lower(COALESCE(value->>'name', '')) = lower(COALESCE(item->>'name', ''))
      AND lower(COALESCE(value->>'brand', '')) = lower(COALESCE(item->>'brand', ''))
    )
    LIMIT 1;

    IF match_index IS NULL THEN
      merged := merged || jsonb_build_array(item);
    ELSE
      merged := jsonb_set(
        merged,
        ARRAY[match_index::text],
        jsonb_strip_nulls(
          existing
          || item
          || jsonb_build_object(
            'quantity',
            COALESCE((existing->>'quantity')::integer, 0)
            + COALESCE((item->>'quantity')::integer, 0),
            'checked',
            COALESCE((existing->>'checked')::boolean, false)
            OR COALESCE((item->>'checked')::boolean, false),
            'purchased',
            COALESCE((existing->>'purchased')::boolean, false)
            OR COALESCE((item->>'purchased')::boolean, false),
            'found_deals',
            COALESCE(item->'found_deals', existing->'found_deals', '[]'::jsonb)
          )
        )
      );
    END IF;
  END LOOP;

  RETURN merged;
END;
$$;

WITH canonical_lists AS (
  SELECT DISTINCT ON (user_id)
    id,
    user_id
  FROM public.shopping_lists
  WHERE user_id IS NOT NULL
  ORDER BY user_id, updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
),
extra_lists AS (
  SELECT sl.id, sl.user_id, cl.id AS canonical_id, sl.items
  FROM public.shopping_lists sl
  JOIN canonical_lists cl ON cl.user_id = sl.user_id
  WHERE sl.user_id IS NOT NULL
    AND sl.id <> cl.id
)
UPDATE public.shopping_lists target
SET items = public.merge_shopping_list_items(target.items, extras.merged_items),
    updated_at = now()
FROM (
  SELECT canonical_id, jsonb_agg(items) AS aggregated_items
  FROM extra_lists
  GROUP BY canonical_id
) grouped
CROSS JOIN LATERAL (
  SELECT jsonb_agg(value) AS merged_items
  FROM jsonb_array_elements(
    COALESCE(grouped.aggregated_items, '[]'::jsonb)
  ) value
) extras
WHERE target.id = grouped.canonical_id;

WITH canonical_lists AS (
  SELECT DISTINCT ON (user_id)
    id,
    user_id
  FROM public.shopping_lists
  WHERE user_id IS NOT NULL
  ORDER BY user_id, updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
)
UPDATE public.list_members lm
SET list_id = cl.id
FROM public.shopping_lists sl
JOIN canonical_lists cl ON cl.user_id = sl.user_id
WHERE lm.list_id = sl.id
  AND sl.user_id IS NOT NULL
  AND sl.id <> cl.id;

WITH canonical_lists AS (
  SELECT DISTINCT ON (user_id)
    id,
    user_id
  FROM public.shopping_lists
  WHERE user_id IS NOT NULL
  ORDER BY user_id, updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
)
UPDATE public.list_invites li
SET list_id = cl.id
FROM public.shopping_lists sl
JOIN canonical_lists cl ON cl.user_id = sl.user_id
WHERE li.list_id = sl.id
  AND sl.user_id IS NOT NULL
  AND sl.id <> cl.id;

WITH canonical_lists AS (
  SELECT DISTINCT ON (user_id)
    id,
    user_id
  FROM public.shopping_lists
  WHERE user_id IS NOT NULL
  ORDER BY user_id, updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
)
DELETE FROM public.shopping_lists sl
USING canonical_lists cl
WHERE sl.user_id = cl.user_id
  AND sl.id <> cl.id;

DROP TRIGGER IF EXISTS prevent_default_list_rename_trigger ON public.shopping_lists;
DROP FUNCTION IF EXISTS public.prevent_default_list_rename();
DROP FUNCTION IF EXISTS public.create_list(text);

DROP POLICY IF EXISTS "lists_delete" ON public.shopping_lists;

ALTER TABLE public.list_invites
  DROP COLUMN IF EXISTS token;

DROP INDEX IF EXISTS public.idx_list_invites_token;
DROP INDEX IF EXISTS public.idx_shopping_lists_one_default_per_user;
DROP INDEX IF EXISTS public.idx_user_profiles_active_list_id;

ALTER TABLE public.shopping_lists
  DROP COLUMN IF EXISTS is_default;

ALTER TABLE public.user_profiles
  DROP COLUMN IF EXISTS active_list_id;

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  created_list_id UUID;
BEGIN
  INSERT INTO public.user_profiles (
    id,
    display_name,
    home_address,
    home_city,
    home_province,
    home_postal_code,
    role,
    managed_supermarket_id
  )
  VALUES (
    NEW.id,
    COALESCE(
      NEW.raw_user_meta_data->>'display_name',
      split_part(NEW.email, '@', 1)
    ),
    COALESCE(NEW.raw_user_meta_data->>'home_address', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_city', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_province', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_postal_code', ''),
    'customer',
    NULL
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT shopping_lists.id
    INTO created_list_id
  FROM public.shopping_lists
  WHERE shopping_lists.user_id = NEW.id
  ORDER BY shopping_lists.created_at ASC NULLS LAST, shopping_lists.id ASC
  LIMIT 1;

  IF created_list_id IS NULL THEN
    INSERT INTO public.shopping_lists (user_id, name, items, is_active)
    VALUES (NEW.id, 'Lista principale', '[]'::jsonb, true)
    RETURNING id INTO created_list_id;
  END IF;

  INSERT INTO public.list_members (list_id, user_id, role)
  VALUES (created_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  RETURN NEW;
END;
$$;


-- Source: supabase/migrations/20260606100000_restore_active_list_selection.sql

ALTER TABLE public.user_profiles
  ADD COLUMN IF NOT EXISTS active_list_id UUID REFERENCES public.shopping_lists(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_user_profiles_active_list_id
  ON public.user_profiles(active_list_id);

UPDATE public.user_profiles up
SET active_list_id = sl.id
FROM public.shopping_lists sl
WHERE sl.user_id = up.id
  AND up.active_list_id IS NULL;


-- Source: supabase/migrations/20260606123000_owner_list_naming_alignment.sql

ALTER TABLE public.shopping_lists
  ALTER COLUMN name SET DEFAULT 'La mia lista';

UPDATE public.shopping_lists
SET name = 'La mia lista'
WHERE name = 'Lista principale';

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  created_list_id uuid;
BEGIN
  INSERT INTO public.user_profiles (
    id,
    display_name,
    home_address,
    home_city,
    home_province,
    home_postal_code,
    role,
    managed_supermarket_id
  )
  VALUES (
    NEW.id,
    COALESCE(
      NEW.raw_user_meta_data->>'display_name',
      split_part(NEW.email, '@', 1)
    ),
    COALESCE(NEW.raw_user_meta_data->>'home_address', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_city', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_province', ''),
    COALESCE(NEW.raw_user_meta_data->>'home_postal_code', ''),
    'customer',
    NULL
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT shopping_lists.id
    INTO created_list_id
  FROM public.shopping_lists
  WHERE shopping_lists.user_id = NEW.id
  ORDER BY shopping_lists.created_at ASC NULLS LAST, shopping_lists.id ASC
  LIMIT 1;

  IF created_list_id IS NULL THEN
    INSERT INTO public.shopping_lists (user_id, name, items, is_active)
    VALUES (NEW.id, 'La mia lista', '[]'::jsonb, true)
    RETURNING id INTO created_list_id;
  END IF;

  INSERT INTO public.list_members (list_id, user_id, role)
  VALUES (created_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  RETURN NEW;
END;
$$;


-- Source: supabase/migrations/20260608100000_contact_requests_mail_flow.sql

-- Migration: replace flyer_requests table with mail-based contact flow
--
-- Adds a private storage bucket for contact attachments and removes the
-- legacy flyer_requests persistence table.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'storage'
      AND table_name = 'buckets'
      AND column_name = 'file_size_limit'
  ) THEN
    INSERT INTO storage.buckets (
      id,
      name,
      public,
      file_size_limit,
      allowed_mime_types
    )
    VALUES (
      'contact-attachments',
      'contact-attachments',
      false,
      10485760,
      ARRAY['image/jpeg', 'image/png', 'application/pdf']
    )
    ON CONFLICT (id) DO UPDATE
    SET
      public = EXCLUDED.public,
      file_size_limit = EXCLUDED.file_size_limit,
      allowed_mime_types = EXCLUDED.allowed_mime_types;
  ELSE
    INSERT INTO storage.buckets (
      id,
      name,
      public
    )
    VALUES (
      'contact-attachments',
      'contact-attachments',
      false
    )
    ON CONFLICT (id) DO UPDATE
    SET public = EXCLUDED.public;
  END IF;
END
$$;

DROP TABLE IF EXISTS public.flyer_requests CASCADE;


-- Source: supabase/migrations/20260608123000_drop_contact_attachments_bucket.sql

-- Migration: remove obsolete contact attachment storage bucket
--
-- Bug report screenshots are now sent as direct SMTP attachments, so the
-- private storage bucket is no longer needed.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM storage.buckets
    WHERE id = 'contact-attachments'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM storage.objects
    WHERE bucket_id = 'contact-attachments'
  ) THEN
    PERFORM set_config('storage.allow_delete_query', 'true', true);

    DELETE FROM storage.buckets
    WHERE id = 'contact-attachments';
  END IF;
END
$$;


-- Source: supabase/migrations/20260615124449_rls_policy_advisor_cleanup.sql

DROP POLICY IF EXISTS "flyers_auth_read" ON public.flyers;
CREATE POLICY "flyers_auth_read"
  ON public.flyers
  FOR SELECT
  TO authenticated
  USING (user_id = (SELECT auth.uid()) OR is_public = true);

DROP POLICY IF EXISTS "flyers_auth_insert" ON public.flyers;
CREATE POLICY "flyers_auth_insert"
  ON public.flyers
  FOR INSERT
  TO authenticated
  WITH CHECK (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "flyers_auth_update" ON public.flyers;
CREATE POLICY "flyers_auth_update"
  ON public.flyers
  FOR UPDATE
  TO authenticated
  USING (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "flyers_auth_delete" ON public.flyers;
CREATE POLICY "flyers_auth_delete"
  ON public.flyers
  FOR DELETE
  TO authenticated
  USING (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "lists_select" ON public.shopping_lists;
CREATE POLICY "lists_select"
  ON public.shopping_lists
  FOR SELECT
  TO authenticated
  USING (
    user_id = (SELECT auth.uid())
    OR private.is_list_member(id, (SELECT auth.uid()))
  );

DROP POLICY IF EXISTS "lists_insert" ON public.shopping_lists;
CREATE POLICY "lists_insert"
  ON public.shopping_lists
  FOR INSERT
  TO authenticated
  WITH CHECK (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "lists_update" ON public.shopping_lists;
CREATE POLICY "lists_update"
  ON public.shopping_lists
  FOR UPDATE
  TO authenticated
  USING (private.is_list_member(id, (SELECT auth.uid())));

DROP POLICY IF EXISTS "list_members_select" ON public.list_members;
CREATE POLICY "list_members_select"
  ON public.list_members
  FOR SELECT
  TO authenticated
  USING (private.is_list_member(list_members.list_id, (SELECT auth.uid())));

DROP POLICY IF EXISTS "list_members_insert_owner" ON public.list_members;
CREATE POLICY "list_members_insert_owner"
  ON public.list_members
  FOR INSERT
  TO authenticated
  WITH CHECK (
    private.is_list_owner(list_members.list_id, (SELECT auth.uid()))
    OR (user_id = (SELECT auth.uid()) AND role = 'owner')
  );

DROP POLICY IF EXISTS "list_members_delete_owner" ON public.list_members;
CREATE POLICY "list_members_delete_owner"
  ON public.list_members
  FOR DELETE
  TO authenticated
  USING (private.is_list_owner(list_members.list_id, (SELECT auth.uid())));

DROP POLICY IF EXISTS "list_invites_select" ON public.list_invites;
CREATE POLICY "list_invites_select"
  ON public.list_invites
  FOR SELECT
  TO authenticated
  USING (
    invited_by = (SELECT auth.uid())
    OR invited_user_id = (SELECT auth.uid())
  );

DROP POLICY IF EXISTS "favorites_own" ON public.favorites;
CREATE POLICY "favorites_own"
  ON public.favorites
  FOR ALL
  TO authenticated
  USING (user_id = (SELECT auth.uid()))
  WITH CHECK (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "profiles_own" ON public.user_profiles;
CREATE POLICY "profiles_own"
  ON public.user_profiles
  FOR ALL
  TO authenticated
  USING (id = (SELECT auth.uid()))
  WITH CHECK (id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "push_subscriptions_own" ON public.push_subscriptions;
DROP POLICY IF EXISTS "push_subscriptions_self_manage" ON public.push_subscriptions;
CREATE POLICY "push_subscriptions_self_manage"
  ON public.push_subscriptions
  FOR ALL
  TO authenticated
  USING (user_id = (SELECT auth.uid()))
  WITH CHECK (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "Users manage own purchase history" ON public.purchase_history;
CREATE POLICY "Users manage own purchase history"
  ON public.purchase_history
  FOR ALL
  TO authenticated
  USING (user_id = (SELECT auth.uid()))
  WITH CHECK (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "app_notifications_select_self" ON public.app_notifications;
CREATE POLICY "app_notifications_select_self"
  ON public.app_notifications
  FOR SELECT
  TO authenticated
  USING (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "app_notifications_update_self" ON public.app_notifications;
CREATE POLICY "app_notifications_update_self"
  ON public.app_notifications
  FOR UPDATE
  TO authenticated
  USING (user_id = (SELECT auth.uid()))
  WITH CHECK (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "offers_auth_read" ON public.offers;
CREATE POLICY "offers_auth_read"
  ON public.offers
  FOR SELECT
  TO authenticated
  USING (
    (
      public.offer_is_currently_active(valid_from, valid_to)
      AND is_confirmed = true
      AND offer_kind = 'published_target'
    )
    OR EXISTS (
      SELECT 1
      FROM public.flyers f
      WHERE f.id = offers.flyer_id
        AND (f.is_public = true OR f.user_id = (SELECT auth.uid()))
    )
  );

ALTER FUNCTION public.merge_shopping_list_items(jsonb, jsonb)
  SET search_path = public;


-- Source: supabase/migrations/20260615125807_harden_manager_supermarkets_and_flyer_targets_rls.sql

ALTER TABLE public.manager_supermarkets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.flyer_targets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "manager_supermarkets_deny_all" ON public.manager_supermarkets;
CREATE POLICY "manager_supermarkets_deny_all"
  ON public.manager_supermarkets
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);

DROP POLICY IF EXISTS "flyer_targets_deny_all" ON public.flyer_targets;
CREATE POLICY "flyer_targets_deny_all"
  ON public.flyer_targets
  FOR ALL
  TO public
  USING (false)
  WITH CHECK (false);

