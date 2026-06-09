-- Migration: replace flyer_requests table with mail-based contact flow
--
-- Adds a private storage bucket for contact attachments and removes the
-- legacy flyer_requests persistence table.

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

DROP TABLE IF EXISTS public.flyer_requests CASCADE;
