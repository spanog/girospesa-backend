-- avatars, logos, product-images buckets must have public=true so that
-- /storage/v1/object/public/{bucket}/{path} serves files without auth.
-- The buckets were created in 007_storage_buckets.sql without this flag.
UPDATE storage.buckets
SET public = true
WHERE id IN ('avatars', 'logos', 'product-images');
