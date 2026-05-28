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
