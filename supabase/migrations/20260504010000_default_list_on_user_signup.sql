-- Ensure each new auth user starts with a profile and an empty owned list.

CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
  v_list_id UUID;
BEGIN
  INSERT INTO user_profiles (id, display_name)
  VALUES (
    NEW.id,
    NEW.raw_user_meta_data->>'display_name'
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT shopping_lists.id
  INTO v_list_id
  FROM shopping_lists
  WHERE shopping_lists.user_id = NEW.id
    AND shopping_lists.is_active = true
  LIMIT 1;

  IF v_list_id IS NULL THEN
    INSERT INTO shopping_lists (user_id, name, items, is_active)
    VALUES (NEW.id, 'Lista spesa', '[]'::jsonb, true)
    RETURNING id INTO v_list_id;
  END IF;

  INSERT INTO list_members (list_id, user_id, role)
  VALUES (v_list_id, NEW.id, 'owner')
  ON CONFLICT (list_id, user_id) DO NOTHING;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;
