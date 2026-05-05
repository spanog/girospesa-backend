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
