-- Public buckets serve direct object URLs without a storage.objects SELECT policy.
-- Removing this policy prevents anonymous enumeration of every offer crop.
DROP POLICY IF EXISTS "product_images_read_public" ON storage.objects;
