#!/bin/sh
set -eu

for file in /supabase-webapp-migrations/*.sql; do
  echo "Applying schema migration: $file"
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f "$file"
done

echo "Applying local seed: /supabase-backend/seed.sql"
psql -v ON_ERROR_STOP=1 -U postgres -d postgres -f /supabase-backend/seed.sql
