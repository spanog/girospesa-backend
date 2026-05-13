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
