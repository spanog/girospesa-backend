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
    managed_supermarket_id,
    active_list_id
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
    NULL,
    NULL
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT shopping_lists.id
    INTO created_list_id
  FROM public.shopping_lists
  WHERE shopping_lists.user_id = NEW.id
    AND shopping_lists.is_default = true
  ORDER BY shopping_lists.created_at ASC NULLS LAST, shopping_lists.id ASC
  LIMIT 1;

  IF created_list_id IS NULL THEN
    INSERT INTO public.shopping_lists (user_id, name, items, is_active, is_default)
    VALUES (NEW.id, 'Lista principale', '[]'::jsonb, true, true)
    RETURNING id INTO created_list_id;
  END IF;

  INSERT INTO public.list_members (list_id, user_id, role)
  VALUES (created_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  UPDATE public.user_profiles
  SET active_list_id = created_list_id
  WHERE id = NEW.id
    AND active_list_id IS NULL;

  RETURN NEW;
END;
$$;

ALTER FUNCTION public.products_update_tsv() SET search_path = public;
