-- Existing accounts registered before display_name was sent in signup metadata
-- received their email local part from the profile trigger. Restore their name.
UPDATE public.user_profiles AS profile
SET display_name = trim(concat_ws(
  ' ',
  auth_user.raw_user_meta_data ->> 'first_name',
  auth_user.raw_user_meta_data ->> 'last_name'
))
FROM auth.users AS auth_user
WHERE profile.id = auth_user.id
  AND NULLIF(trim(auth_user.raw_user_meta_data ->> 'display_name'), '') IS NULL
  AND NULLIF(trim(concat_ws(
    ' ',
    auth_user.raw_user_meta_data ->> 'first_name',
    auth_user.raw_user_meta_data ->> 'last_name'
  )), '') IS NOT NULL
  AND (
    NULLIF(trim(profile.display_name), '') IS NULL
    OR trim(profile.display_name) = split_part(auth_user.email, '@', 1)
  );
