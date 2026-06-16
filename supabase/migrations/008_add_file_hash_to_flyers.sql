-- Migration: add file_hash column to flyers

ALTER TABLE flyers ADD COLUMN IF NOT EXISTS file_hash TEXT;