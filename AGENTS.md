# Backend agent notes

## Testing Guardrails

- Every backend change must update the closest test layer first: unit/service for pure logic, integration for real DB/API contracts.
- Stable JSON responses touched by the change should gain or update contract snapshots under `tests/__snapshots__/` or `tests/integration/__snapshots__/`.
- Normalize unstable values before snapshot comparison: UUID, token, timestamp, variable URL host/query.
- Supabase schema or RLS changes must keep `supabase db advisors --local` clean for touched areas; wrap `auth.uid()` / `auth.jwt()` as `select` expressions in policies when possible to avoid advisor performance warnings.
- Snapshot tests support, not replace, explicit assertions on permissions, ordering, lifecycle transitions, and domain invariants.
- Keep notification flows aligned across transports: `favorite_offer` logic must stay shared between the `/push/notify-favorites` webhook path and any local/development fallback executed during flyer publication.
- `favorite_offer` should follow anti-spam semantics: aggregate multiple matches from the same flyer into one notification per `user + flyer`, updating the existing row/push payload instead of inserting one card per matched product.
- Run:
  - `.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance`
  - `.venv/bin/python -m pytest tests/integration -v`

## Deploy / CI conventions

- Production/runtime baseline is Python `3.14.3`: keep `.python-version`, `pyproject.toml`, `render.yaml`, runtime guards, and CI aligned when changing interpreter support.
- Supabase schema source of truth for this repo is `girospesa-backend/supabase/migrations/`; keep one active baseline or forward-only migration chain there, and archive any retired history outside that directory.
- Keep `render.yaml` aligned with runtime expectations and required env vars.
- GitHub Actions under `.github/workflows/` are part of the production contract: update them when commands, Python version, or test entrypoints change.
- Production Supabase migrations are deployed by `.github/workflows/supabase-db-production.yml`; when schema deployment assumptions change, update workflow, README secrets list, and guard tests together.
- Scheduled maintenance for free-tier production uses `POST /ops/cron/daily-maintenance` with `X-Ops-Secret`; if cleanup logic changes, keep the route and workflow in sync.
- Keep `POST /ops/cron/daily-maintenance` best-effort: one failing cleanup step must not block the others, and workflow logs must preserve the response body for production debugging.
- Free-tier Render anti-idle relies on `.github/workflows/render-keepalive.yml` pinging `BACKEND_HEALTHCHECK_URL` every 10 minutes; if hostnames or sleep strategy change, update workflow, secrets docs, and tests together.
