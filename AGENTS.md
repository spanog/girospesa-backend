# Backend agent notes

## Scope

- `README.md` = human-facing documentation only: architecture, setup, configuration, deploy, runbooks, and API behavior notes.
- `AGENTS.md` = agent-facing operating rules only: commands, test expectations, deploy guardrails, and repo workflow conventions.
- When a change affects both domains, update both files but keep each change inside its own scope.

## Testing Guardrails

- Every backend change must update the closest test layer first: unit/service for pure logic, integration for real DB/API contracts.
- Stable JSON responses touched by the change should gain or update contract snapshots under `tests/__snapshots__/` or `tests/integration/__snapshots__/`.
- Normalize unstable values before snapshot comparison: UUID, token, timestamp, variable URL host/query.
- Supabase schema or RLS changes must keep `supabase db advisors --local` clean for touched areas; wrap `auth.uid()` / `auth.jwt()` as `select` expressions in policies when possible to avoid advisor performance warnings.
- Snapshot tests support, not replace, explicit assertions on permissions, ordering, lifecycle transitions, and domain invariants.
- Keep notification flows aligned across transports: `favorite_offer` logic must stay shared between the `/push/notify-favorites` webhook path and any local/development fallback executed during flyer publication.
- `favorite_offer` should follow anti-spam semantics: aggregate multiple matches from the same flyer into one notification per `user + flyer`, updating the existing row/push payload instead of inserting one card per matched product.
- Multi-page extraction resume is part of the backend contract: if at least one PDF chunk has already been persisted, generic transient runtime failures (for example `httpx` / Supabase read errors) must preserve `next_chunk_*`, `partial_products_count`, and a resumable retry path instead of forcing chunk 1 to rerun.
- Gemini retry policy is part of that contract too: provider-side transient `500/502/504` and `503/UNAVAILABLE` failures must use exponential backoff with jitter so the backend does not burn all retries in a few seconds during temporary provider instability.
- A `processing` flyer with persisted `last_completed_chunk` + `next_chunk_*` that stays stale after a web-service restart must be manually retriggerable through the same extract endpoint; stale in-flight state is not a permanent lock.
- Startup recovery is part of the production contract: when the web service boots, it must scan orphaned `processing` flyers left by the previous instance, queue automatic resume for rows with a saved chunk checkpoint, and fail fast rows that died before the first checkpoint.
- Completed extraction is also part of that contract: once all chunks have already been persisted, late runtime failures in final status updates or post-success side effects must keep the flyer terminally `done` instead of degrading it back to `error`.
- Flyer confirmation is part of the publish contract: `POST /flyers/{flyer_id}/offers/confirm` must confirm every source offer first, then sync all derived `published_target` clones in an idempotent pass so rerunning confirm can finish missing public offers after an interrupted publish.
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
