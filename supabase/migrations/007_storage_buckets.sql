-- Migration: storage_buckets
--
-- Crea i bucket Supabase Storage e le policy RLS su storage.objects.
-- Il service_role bypassa sempre RLS → non serve policy esplicita per i write
-- da FastAPI. Le policy qui controllano solo anon e authenticated.
--
-- Convenzione percorsi file:
--   flyers/          {user_id}/{flyer_id}.{ext}
--   avatars/         {user_id}.{ext}
--   logos/           {supermarket_slug}.png
--   product-images/  {product_id}.{ext}

-- Current Supabase self-hosted schema exposes only id/name/owner/timestamps on
-- storage.buckets. Public/private access comes from storage.objects RLS below.
INSERT INTO storage.buckets (id, name, public)
VALUES
  ('flyers',          'flyers',          false),
  ('avatars',         'avatars',         true),
  ('logos',           'logos',           true),
  ('product-images',  'product-images',  true)
ON CONFLICT (id) DO NOTHING;

-- ── Policy: bucket flyers (privato) ──────────────────────────────────────────
-- Upload: solo service_role (FastAPI) → bypassa RLS, nessuna policy necessaria
-- Download: solo il proprietario del file (path inizia con il proprio user_id)
-- Delete: solo service_role

CREATE POLICY "flyers_read_owner"
  ON storage.objects FOR SELECT
  TO authenticated
  USING (
    bucket_id = 'flyers'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

-- anon: nessun accesso al bucket flyers (deny by default con RLS abilitato)

-- ── Policy: bucket avatars (pubblico in lettura) ──────────────────────────────
-- Lettura pubblica gestita via policy esplicita.
CREATE POLICY "avatars_read_public"
  ON storage.objects FOR SELECT
  TO anon, authenticated
  USING (bucket_id = 'avatars');

-- ── Policy: bucket logos (pubblico in lettura) ────────────────────────────────
CREATE POLICY "logos_read_public"
  ON storage.objects FOR SELECT
  TO anon, authenticated
  USING (bucket_id = 'logos');

-- ── Policy: bucket product-images (pubblico in lettura) ──────────────────────
CREATE POLICY "product_images_read_public"
  ON storage.objects FOR SELECT
  TO anon, authenticated
  USING (bucket_id = 'product-images');
