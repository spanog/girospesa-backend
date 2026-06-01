SET lock_timeout = '5s';

ALTER TABLE public.user_profiles
  ADD COLUMN IF NOT EXISTS notifications_enabled BOOLEAN NOT NULL DEFAULT true;

UPDATE public.user_profiles
SET notifications_enabled =
  COALESCE(notification_deals, true)
  OR COALESCE(notification_favorites, true)
  OR COALESCE(notification_shared_lists, true);

ALTER TABLE public.user_profiles
  DROP COLUMN IF EXISTS notification_deals,
  DROP COLUMN IF EXISTS notification_favorites,
  DROP COLUMN IF EXISTS notification_shared_lists;
