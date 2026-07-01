ALTER TABLE public.push_subscriptions
  ADD COLUMN IF NOT EXISTS channel text NOT NULL DEFAULT 'web_push',
  ADD COLUMN IF NOT EXISTS token text,
  ADD COLUMN IF NOT EXISTS platform text,
  ADD COLUMN IF NOT EXISTS device_id text;

UPDATE public.push_subscriptions
SET channel = 'web_push'
WHERE channel IS NULL;

ALTER TABLE public.push_subscriptions
  DROP CONSTRAINT IF EXISTS push_subscriptions_channel_check,
  ADD CONSTRAINT push_subscriptions_channel_check
    CHECK (channel IN ('web_push', 'native_fcm'));

ALTER TABLE public.push_subscriptions
  DROP CONSTRAINT IF EXISTS push_subscriptions_web_shape_check,
  ADD CONSTRAINT push_subscriptions_web_shape_check
    CHECK (
      channel <> 'web_push'
      OR (endpoint IS NOT NULL AND p256dh IS NOT NULL AND auth_key IS NOT NULL)
    );

ALTER TABLE public.push_subscriptions
  DROP CONSTRAINT IF EXISTS push_subscriptions_native_shape_check,
  ADD CONSTRAINT push_subscriptions_native_shape_check
    CHECK (channel <> 'native_fcm' OR token IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS push_subscriptions_user_native_token_idx
  ON public.push_subscriptions (user_id, token)
  WHERE channel = 'native_fcm';

CREATE INDEX IF NOT EXISTS push_subscriptions_user_channel_idx
  ON public.push_subscriptions (user_id, channel);
