ALTER TABLE public.shopping_lists
  ALTER COLUMN name SET DEFAULT 'La mia lista';

UPDATE public.shopping_lists
SET name = 'La mia lista'
WHERE name = 'Lista principale';

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  created_list_id uuid;
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
    VALUES (NEW.id, 'La mia lista', '[]'::jsonb, true)
    RETURNING id INTO created_list_id;
  END IF;

  INSERT INTO public.list_members (list_id, user_id, role)
  VALUES (created_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  RETURN NEW;
END;
$$;
