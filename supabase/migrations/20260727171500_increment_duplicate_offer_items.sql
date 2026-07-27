CREATE OR REPLACE FUNCTION public.append_list_item(
  p_list_id UUID,
  p_item JSONB
)
RETURNS VOID AS $$
BEGIN
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
ALTER FUNCTION public.append_list_item(uuid, jsonb) SET search_path = public;
