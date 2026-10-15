# Backend agent notes

## Scope

- `README.md` = human-facing entry point only: overview, quick start, and links to detailed docs.
- `docs/*.md` = human-facing documentation by responsibility: architecture, API, local development, testing, configuration, data model, jobs, flows, and deploy runbooks.
- `AGENTS.md` = agent-facing operating rules only: commands, test expectations, deploy guardrails, and repo workflow conventions.
- When a change affects both domains, update both files but keep each change inside its own scope.

## Testing Guardrails

- Every backend change must update the closest test layer first: unit/service for pure logic, integration for real DB/API contracts.
- Stable JSON responses touched by the change should gain or update contract snapshots under `tests/__snapshots__/` or `tests/integration/__snapshots__/`.
- Normalize unstable values before snapshot comparison: UUID, token, timestamp, variable URL host/query.
- Supabase schema or RLS changes must keep `supabase db advisors --local` clean for touched areas; wrap `auth.uid()` / `auth.jwt()` as `select` expressions in policies when possible to avoid advisor performance warnings.
- Snapshot tests support, not replace, explicit assertions on permissions, ordering, lifecycle transitions, and domain invariants.
- `GET /offers` deve risolvere l'area soltanto dal Comune firmato guest o dal profilo e applicare il raggio prima di paginare le offerte.
- `append_list_item` deve incrementare atomically una riga attiva con lo stesso `pinned_offer_id`, senza unire righe già acquistate.
- Cookie località guest: richieste da origin HTTPS Capacitor devono ricevere `SameSite=None; Secure`; mantenere test endpoint per questo contratto cross-site.
- Notification delivery: mantenere batch e concorrenza configurabili; ogni chiamata push deve avere una deadline e un worker terminato non può lasciare job `processing` senza recovery.

## Commands

- Setup local env files: `cp .env.example .env && cp .env.test.example .env.test`
- Start Supabase local stack: `supabase start`
- Run app locally: `.venv/bin/python -m uvicorn main:app --reload --port 8000`
- Seed/check admin: `.venv/bin/python -m scripts.seed_admin` / `.venv/bin/python -m scripts.seed_admin --check`
- Run:
  - `.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance`
- `.venv/bin/python -m pytest tests/integration -v`
  - `RUN_PERFORMANCE_TESTS=1 .venv/bin/python -m pytest tests/performance -v -s` for opt-in benchmarks
- Manage integration stack manually: `.venv/bin/python -m scripts.integration_stack up|down|status|env`

## Data Access

- FastAPI is the only application layer allowed to touch database persistence details.
- Frontend-facing features must expose backend endpoints instead of coupling UI code to Supabase tables/RPCs directly.
- Non introdurre provider, endpoint o client di geocoding: utenti e filiali usano esclusivamente codici Comune ISTAT.
- Keep raw SQL, PostgREST, Supabase service-role access, and schema-specific branching inside backend repositories/services, never inside frontend code.
- No endpoint may trust client-supplied `admin`, `manager`, `role`, or similar flags for privileges or data scope. Authorization must derive from validated server-side auth context.
- Public contact flows (`/contact-requests`) are mail-first: do not reintroduce app tables or client-side inserts for bug reports, collaboration requests, or missing-flyer requests.

## Integration Test Isolation

- `tests/integration/` must boot a fresh Docker stack on project `girospesa-itest` and destroy only that stack with `down -v --remove-orphans`.
- Integration env overrides (`SUPABASE_URL`, `DB_DSN`, keys, etc.) must stay scoped to the `pytest` session and be restored afterward. Never mutate process env at module import time.
- Dev stack (`supabase start`, backend on `.env`, local ports `54321+`) must remain untouched by integration test setup/teardown.

## Supabase Conventions

- Match pinned client API exactly. Example: PostgREST `.order()` expects `nullsfirst`, not `nulls_first`.
- Data API grants are opt-in. Every migration creating a `public` table, sequence, or function must grant only the operations required by each caller in that same migration; include `service_role` when FastAPI accesses it through Supabase. Do not restore default grants. RLS policies never replace grants.
- Local Supabase API exposure stays limited to `public` schema. `pg_graphql` is disabled; do not build or document `/graphql/v1` flows.
- RLS-only helper functions that need `SECURITY DEFINER` privileges must live in a non-exposed schema such as `private`; do not publish them from `public` or document them as client-callable RPCs.
- Public Storage buckets (`avatars`, `logos`, `product-images`) rely on signed-less `/storage/v1/object/public/...` URLs only. Do not depend on anonymous bucket listing via `storage.objects` policies.
- Le anteprime dei volantini pubblici passano da `GET /flyers/{id}/preview`: restituire binario WebP con cache HTTP, mai URL Storage al browser. Conservare gli URL firmati brevi esclusivamente in endpoint separati per workflow admin privati.

## Deploy / CI conventions

- Production/runtime baseline is Python `3.14.3`: keep `.python-version`, `pyproject.toml`, `render.yaml`, runtime guards, and CI aligned when changing interpreter support.
- Backend CI exposes `performance-test` only through manual `workflow_dispatch`; keep performance benchmarks opt-in unless thresholds become stable enough for every PR.
- Supabase schema source of truth for this repo is `girospesa-backend/supabase/migrations/`; keep one active baseline or forward-only migration chain there, and archive any retired history outside that directory.
- Keep `render.yaml` aligned with runtime expectations and required env vars.
- GitHub Actions under `.github/workflows/` are part of the production contract: update them when commands, Python version, or test entrypoints change.
- Production Supabase migrations are deployed by `.github/workflows/supabase-db-production.yml`; when schema deployment assumptions change, update workflow, deploy/configuration docs, and guard tests together.

## Git / Ignore Rules

- Keep `main` clean for deploy-ready code.
- Never commit local secrets or machine-specific env files.
- Track `.env.example` and `.env.test.example`.
- Ignore `.env`, `.env.local`, `.env.test`, Python caches, coverage artifacts, editor files, macOS files, and Supabase local state.

## Local extraction provider

- `LLM_PROVIDER=ollama` selects the local `/api/chat` vision adapter; Gemini remains the default for deployment.
- Test Ollama HTTP contracts with `httpx.MockTransport`; unit tests must never download models or contact a real inference server.
- Preserve PDF chunk callbacks, absolute page indices and resume metadata when changing providers.
- Standalone extraction: `.venv/bin/python -m scripts.extraction.submit_flyer <file> --provider ollama --model <tag> --output /tmp/extraction.json` (no Supabase writes).
- Live model smoke tests are manual checks, separate from automated tests; record model tag and avoid claiming quality from a single sample.
- Settings tests that assert defaults must clear the corresponding environment variables; `_env_file=None` does not disable values already loaded into the process environment.
- `gemma4:26b-mlx`: local operational constraint is `OLLAMA_CHUNK_SIZE_PAGES=1` / CLI `--chunk-pages 1`. Two-page inference produced wrong-product boxes; single-page run improved sampled localization. Do not increase chunk size for longer PDFs. This is a documented constraint, not a runtime guard. Preserve named XY coordinates and grouped-offer prompt; rollback needs a controlled comparison, never reinterpret stored version-2 boxes as legacy YX. Evidence: `docs/local-model-evaluation.md`.

## Bounding-box contract

- Internal/new storage coordinates are `[x_min, y_min, x_max, y_max]` on the 0–1000 grid, version 2. Model responses use named fields.
- Never infer an old row’s axis order from model identity: use `packshot_bbox_version`, defaulting missing historical values to version 1 (YX).
- Preserve crop geometry for old rows and copy version metadata during SQL publication. Test asymmetric boxes on non-square pages and both API storage versions.
- Apply coordinate migration before running new extraction code; deploy canonical interactive-offer API and frontend reader together.

- Granularità estrazione: prompt principale e recovery devono accorpare alternative della stessa linea solo a formato/prezzo/validità/condizioni uguali. Coprire Accadì 1%/senza grassi come singola offerta da 1 L, senza falso multipack e senza `format.varianti`.

- Coprire anche gusti yogurt accorpati con vero multipack 2x125 g: raggruppamento non deve ridurre quantità a 125 g o sommare alternative a 4x125 g.

- `OLLAMA_CHUNK_SIZE_PAGES=2` deve inviare entrambe le immagini in una sola chiamata. Coprire mapping pagine relativo/assoluto, indici invalidi, resume e pagina finale dispari. Non assegnare arbitrariamente prodotti batch a pagina 1. CLI: `--chunk-pages 1|2`.
