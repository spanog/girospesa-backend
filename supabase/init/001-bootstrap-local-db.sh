#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
ALTER TABLE storage.buckets
  ADD COLUMN IF NOT EXISTS public boolean NOT NULL DEFAULT false;
SQL

for file in /supabase-webapp-migrations/*.sql; do
  echo "Applying schema migration: $file"
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f "$file"
done

echo "Applying local seed: /supabase-backend/seed.sql"
psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /supabase-backend/seed.sql

psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
ALTER FUNCTION public.offers_compute_fields() SET search_path = public;
SQL
