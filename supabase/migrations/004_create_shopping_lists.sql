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
