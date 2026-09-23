#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
ALTER TABLE storage.buckets
  ADD COLUMN IF NOT EXISTS public boolean NOT NULL DEFAULT false;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES
  FROM anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE USAGE, SELECT ON SEQUENCES
  FROM anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE EXECUTE ON FUNCTIONS
  FROM PUBLIC, anon, authenticated, service_role;
SQL

for file in /supabase-migrations/*.sql; do
  echo "Applying schema migration: $file"
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f "$file"
done

echo "Applying local seed: /supabase-backend/seed.sql"
psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /supabase-backend/seed.sql

psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
ALTER FUNCTION public.offers_compute_fields() SET search_path = public;
NOTIFY pgrst, 'reload schema';
SQL
