-- Fix search_path for SECURITY DEFINER function handle_new_user
-- Without this, the function can't find 'user_profiles' when called from auth.users trigger
ALTER FUNCTION handle_new_user() SET search_path = public;
