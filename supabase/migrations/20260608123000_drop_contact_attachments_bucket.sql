-- Migration: remove obsolete contact attachment storage bucket
--
-- Bug report screenshots are now sent as direct SMTP attachments, so the
-- private storage bucket is no longer needed.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM storage.buckets
    WHERE id = 'contact-attachments'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM storage.objects
    WHERE bucket_id = 'contact-attachments'
  ) THEN
    PERFORM set_config('storage.allow_delete_query', 'true', true);

    DELETE FROM storage.buckets
    WHERE id = 'contact-attachments';
  END IF;
END
$$;
