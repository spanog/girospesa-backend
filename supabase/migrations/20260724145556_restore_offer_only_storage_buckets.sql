-- Restore buckets intentionally emptied during the one-time offer-only reset.
-- `storage rm --recursive` may remove an empty bucket after its objects.
INSERT INTO storage.buckets (id, name, public)
VALUES
  ('flyers', 'flyers', false),
  ('product-images', 'product-images', true)
ON CONFLICT (id) DO UPDATE
SET public = EXCLUDED.public;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'storage'
      AND tablename = 'objects'
      AND policyname = 'product_images_read_public'
  ) THEN
    CREATE POLICY "product_images_read_public"
      ON storage.objects FOR SELECT
      TO anon, authenticated
      USING (bucket_id = 'product-images');
  END IF;
END
$$;
