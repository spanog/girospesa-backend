CREATE TABLE public.list_sync_operations (
  list_id UUID NOT NULL REFERENCES public.shopping_lists(id) ON DELETE CASCADE,
  operation_id UUID NOT NULL,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (list_id, operation_id)
);

ALTER TABLE public.list_sync_operations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "list members add sync operations"
  ON public.list_sync_operations
  FOR INSERT
  TO authenticated
  WITH CHECK (
    user_id = (SELECT auth.uid())
    AND EXISTS (
      SELECT 1
      FROM public.list_members
      WHERE list_id = list_sync_operations.list_id
        AND user_id = (SELECT auth.uid())
    )
  );

CREATE POLICY "users read own sync operations"
  ON public.list_sync_operations
  FOR SELECT
  TO authenticated
  USING (user_id = (SELECT auth.uid()));

REVOKE ALL ON TABLE public.list_sync_operations FROM anon;
GRANT SELECT, INSERT ON TABLE public.list_sync_operations TO authenticated;

CREATE OR REPLACE FUNCTION public.append_list_item(
  p_list_id UUID,
  p_item JSONB,
  p_operation_id UUID
)
RETURNS VOID AS $$
DECLARE
  operation_inserted BOOLEAN;
BEGIN
  IF p_operation_id IS NOT NULL THEN
    INSERT INTO public.list_sync_operations (list_id, operation_id, user_id)
    VALUES (p_list_id, p_operation_id, auth.uid())
    ON CONFLICT (list_id, operation_id) DO NOTHING
    RETURNING true INTO operation_inserted;

    IF NOT COALESCE(operation_inserted, false) THEN
      RETURN;
    END IF;
  END IF;

  UPDATE public.shopping_lists
  SET items = CASE
    WHEN p_item->>'source' = 'offer'
      AND p_item->>'pinned_offer_id' IS NOT NULL
      AND EXISTS (
        SELECT 1
        FROM jsonb_array_elements(COALESCE(items, '[]'::jsonb)) item
        WHERE item->>'source' = 'offer'
          AND item->>'pinned_offer_id' = p_item->>'pinned_offer_id'
          AND COALESCE((item->>'purchased')::boolean, false) = false
      )
    THEN (
      SELECT jsonb_agg(
        CASE
          WHEN item->>'source' = 'offer'
            AND item->>'pinned_offer_id' = p_item->>'pinned_offer_id'
            AND COALESCE((item->>'purchased')::boolean, false) = false
            AND item->>'id' = (
              SELECT candidate->>'id'
              FROM jsonb_array_elements(COALESCE(items, '[]'::jsonb)) candidate
              WHERE candidate->>'source' = 'offer'
                AND candidate->>'pinned_offer_id' = p_item->>'pinned_offer_id'
                AND COALESCE((candidate->>'purchased')::boolean, false) = false
              LIMIT 1
            )
          THEN jsonb_set(
            item,
            '{quantity}',
            to_jsonb(
              COALESCE((item->>'quantity')::numeric, 0)
              + COALESCE((p_item->>'quantity')::numeric, 1)
            )
          )
          ELSE item
        END
        ORDER BY position
      )
      FROM jsonb_array_elements(COALESCE(items, '[]'::jsonb))
        WITH ORDINALITY AS entries(item, position)
    )
    ELSE COALESCE(items, '[]'::jsonb) || jsonb_build_array(p_item)
  END,
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

ALTER FUNCTION public.append_list_item(UUID, JSONB, UUID) SET search_path = public;
REVOKE EXECUTE ON FUNCTION public.append_list_item(UUID, JSONB, UUID) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.append_list_item(UUID, JSONB, UUID) TO authenticated;

CREATE OR REPLACE FUNCTION public.append_list_item(
  p_list_id UUID,
  p_item JSONB
)
RETURNS VOID AS $$
BEGIN
  PERFORM public.append_list_item(p_list_id, p_item, NULL);
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;

ALTER FUNCTION public.append_list_item(UUID, JSONB) SET search_path = public;
REVOKE EXECUTE ON FUNCTION public.append_list_item(UUID, JSONB) FROM public, anon;
GRANT EXECUTE ON FUNCTION public.append_list_item(UUID, JSONB) TO authenticated;

ALTER TABLE public.purchase_history
  ADD COLUMN IF NOT EXISTS offline_operation_id UUID;

CREATE UNIQUE INDEX purchase_history_offline_operation_id_idx
  ON public.purchase_history (offline_operation_id)
  WHERE offline_operation_id IS NOT NULL;
