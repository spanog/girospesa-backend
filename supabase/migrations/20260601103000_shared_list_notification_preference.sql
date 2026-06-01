ALTER TABLE public.user_profiles
ADD COLUMN IF NOT EXISTS notification_shared_lists BOOLEAN DEFAULT true;

UPDATE public.user_profiles
SET notification_shared_lists = true
WHERE notification_shared_lists IS NULL;
