-- Migration: remove obsolete contact attachment storage bucket
--
-- Bug report screenshots are now sent as direct SMTP attachments, so the
-- private storage bucket is no longer needed.

DELETE FROM storage.objects
WHERE bucket_id = 'contact-attachments';

DELETE FROM storage.buckets
WHERE id = 'contact-attachments';
