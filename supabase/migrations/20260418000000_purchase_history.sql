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
