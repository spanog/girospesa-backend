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
