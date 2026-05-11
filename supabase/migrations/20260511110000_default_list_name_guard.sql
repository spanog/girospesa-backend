ALTER TABLE public.shopping_lists
  ALTER COLUMN name SET DEFAULT 'Lista principale';

UPDATE public.shopping_lists
SET name = 'Lista principale'
WHERE is_default = true;

CREATE OR REPLACE FUNCTION public.prevent_default_list_rename()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.is_default = true AND NEW.name IS DISTINCT FROM OLD.name THEN
    RAISE EXCEPTION 'Default list cannot be renamed';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS prevent_default_list_rename_trigger
  ON public.shopping_lists;

CREATE TRIGGER prevent_default_list_rename_trigger
BEFORE UPDATE ON public.shopping_lists
FOR EACH ROW
WHEN (OLD.is_default = true)
EXECUTE FUNCTION public.prevent_default_list_rename();

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
    '',
    '',
    '',
    '',
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
