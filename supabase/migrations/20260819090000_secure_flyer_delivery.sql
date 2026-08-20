-- Flyer originals are never publicly addressable. The API only issues signed URLs.
INSERT INTO storage.buckets (id, name, public)
VALUES ('flyers', 'flyers', false)
ON CONFLICT (id) DO UPDATE
SET public = false;
