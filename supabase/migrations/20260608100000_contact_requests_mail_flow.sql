-- Migration: replace flyer_requests table with mail-based contact flow
--
-- Adds a private storage bucket for contact attachments and removes the
-- legacy flyer_requests persistence table.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'storage'
      AND table_name = 'buckets'
      AND column_name = 'file_size_limit'
  ) THEN
    INSERT INTO storage.buckets (
      id,
      name,
      public,
      file_size_limit,
      allowed_mime_types
    )
    VALUES (
      'contact-attachments',
      'contact-attachments',
      false,
      10485760,
      ARRAY['image/jpeg', 'image/png', 'application/pdf']
    )
    ON CONFLICT (id) DO UPDATE
    SET
      public = EXCLUDED.public,
      file_size_limit = EXCLUDED.file_size_limit,
      allowed_mime_types = EXCLUDED.allowed_mime_types;
  ELSE
    INSERT INTO storage.buckets (
      id,
      name,
      public
    )
    VALUES (
      'contact-attachments',
      'contact-attachments',
      false
    )
    ON CONFLICT (id) DO UPDATE
    SET public = EXCLUDED.public;
  END IF;
END
$$;

DROP TABLE IF EXISTS public.flyer_requests CASCADE;
