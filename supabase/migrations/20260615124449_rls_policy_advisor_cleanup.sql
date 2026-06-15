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
