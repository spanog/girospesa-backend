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
