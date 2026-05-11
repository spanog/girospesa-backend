# Project Conventions

## Commands

- Setup local env files: `cp .env.example .env && cp .env.test.example .env.test`
- Start Supabase local stack: `supabase start`
- Run app locally: `.venv/bin/python -m uvicorn main:app --reload --port 8000`
- Run unit-style tests: `.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance`
- Run integration tests in isolated Docker stack: `.venv/bin/python -m pytest tests/integration -v`
- Manage integration stack manually: `.venv/bin/python -m scripts.integration_stack up|down|status|env`
- Run performance tests: `.venv/bin/python -m pytest tests/performance -v -s`

## Data Access Rule

- FastAPI is the only application layer allowed to touch database persistence details.
- Frontend-facing features must expose backend endpoints instead of coupling UI code to Supabase tables/RPCs directly.
- Keep raw SQL, PostgREST, Supabase service-role access, and schema-specific branching inside backend repositories/services, never inside frontend code.

## Integration Test Isolation

- `tests/integration/` must boot a fresh Docker stack on project `girospesa-itest` and destroy only that stack with `down -v --remove-orphans`.
- Integration env overrides (`SUPABASE_URL`, `DB_DSN`, keys, etc.) must stay scoped to the `pytest` session and be restored afterward. Never mutate process env at module import time.
- Dev stack (`supabase start`, backend on `.env`, local ports `54321+`) must remain untouched by integration test setup/teardown.

## Git

- Keep `main` clean for deploy-ready code.
- Push ongoing V1 work to a dedicated long-lived branch.
- Never commit local secrets or machine-specific env files.

## Flyer Review

- Manual draft-offer create/update endpoints on `/flyers/{flyer_id}/draft-offers` own canonical product fields too: `name`, `brand`, `category`, `subcategory`, `format`.
- `format` must be structured `ProductFormat` JSON. Plain text format is forbidden.
- Canonical product identity is `name + brand + format_key`; `format_key` and `format_label` are always derived backend-side from normalized `format`.
- Persist `products.format` in compact canonical form only: omit `null`, empty arrays, and default `false` flags when not semantically needed.
- LLM/provider output should keep `format` sparse too: emit only `tipo` plus relevant fields. Backend canonicalization stays authoritative.
- Extraction-only `format.varianti` must be exploded before persistence. Persisted products/offers always point to one concrete format.
- Extraction pipeline should normalize each concrete format once, dedupe on `(name, brand, format_key)` before persistence, and batch-upsert unique products for the flyer.
- For multi-page PDFs, Gemini extraction must split the document into rigid 3-page PDF chunks and process one chunk per request. After each successful chunk, persist draft offers immediately and update `flyers.extraction_metadata` with `pages_processed`, `current_chunk_start`, `current_chunk_end`, `progress_percent`, `chunks_completed`, `chunks_total`, `products_found`, `last_completed_chunk`, and `next_chunk_*`. If one chunk fails after retries, set flyer `status='error'`, keep already saved draft offers, and expose `resume_available` plus `failed_chunk_*` / `next_chunk_*` metadata so the same `POST /flyers/{flyer_id}/extract` call can resume from the first failed chunk. Resume detection must read persisted `extraction_metadata`, because router flips flyer back to `processing` before background task restarts.
- Gemini retry/failure logs must preserve structured provider context when available: exception type, `code`, `status`, `message`, HTTP status/body, and request id. Keep same formatted string in app logs and `retry_errors` sent to `extraction_log`.
- Before upserting, pipeline runs a fuzzy pre-check per `format_key` bucket: `_find_similar_product()` in `ExtractionService` uses `rapidfuzz.fuzz.partial_ratio` on names (≥0.85) and `fuzz.ratio` on diacritic-normalized brands (≥0.90). Thresholds configurable via `product_name_similarity_threshold` / `product_brand_similarity_threshold` in `core/config.py`. Matches reuse the existing `product_id` — no duplicate row is created. `format_key` is always an exact-match gate; never fuzzified.
- Draft and confirmed offer payloads returned by flyer review endpoints must flatten `products.subcategory` alongside `category`.
- Draft and confirmed offer payloads must expose both `format` and `format_label`.
- `GET /products` ordina default per `products.name`; `sort=expiry` ordina per `offers.valid_to` crescente con offerte senza scadenza dopo, poi per `products.name`.
- `GET /products` search RPC `public.search_products_catalog` must preserve fuzzy `word_similarity` ranking but also match prefix/substring queries on product name and brand, so inputs like `mozza` still return `Mozzarella`.
- `flyers.extraction_metadata` should keep live extraction progress during `processing`, then per-stage timing keys (`provider_seconds`, `variant_expansion_seconds`, `normalization_seconds`, `dedupe_seconds`, `product_upsert_seconds`, `offer_insert_seconds`, `total_seconds`) plus product-count and average-format-size telemetry at completion.
- Extraction completion/failure Web Push payloads must include structured `data`: `kind`, `flyer_id`, `status`, `products_count`, and `url`. Frontend admin cache sync depends on those fields.
- Admin product delete on `/admin/products/{id}` is hard delete only when product has zero linked offers; endpoint must also delete linked favorites.
- `purchase_history.product_id` is historical snapshot data, not live FK protection for canonical products.
- `purchase_history.quantity` stores purchased quantity; stored `price_paid`, `price_original`, and `savings` must be quantity-scaled totals, not unit values.

## Supabase Query Builder

- Match pinned client API exactly. Example: PostgREST `.order()` expects `nullsfirst`, not `nulls_first`.
- Local Supabase API exposure stays limited to `public` schema. `pg_graphql` is disabled; do not build or document `/graphql/v1` flows.
- Public Storage buckets (`avatars`, `logos`, `product-images`) rely on signed-less `/storage/v1/object/public/...` URLs only. Do not depend on anonymous bucket listing via `storage.objects` policies.

## Admin Seed

- `scripts.seed_admin` must be idempotent and must ensure the admin has `app_metadata.role = "admin"`, `public.user_profiles.role = 'admin'`, address `Via Palmiro Togliatti, 89024 Polistena (RC)`, one default empty owner shopping list named `Lista principale`, and `user_profiles.active_list_id` aligned to that default list.
- New auth users must get one default empty owner shopping list named `Lista principale` from the DB signup trigger, plus `user_profiles.active_list_id` pointing to it; keep this in sync with shared Supabase migrations.

## Optimizer

- `/optimize` must resolve `pinned_offer_id` before fuzzy matching. A list item added from an offer is an exact offer match (`match_score=1.0`) when the offer is active and in range. When the frontend selects an optimization alternative, `PATCH /lists/{list_id}/items/{item_id}` must persist the new `pinned_offer_id`, `pinned_product_id`, `found_deals`, category, and subcategory through RPC `update_list_item`, then reread the saved list item so lista, giro spesa, acquisti, and freshness stay aligned. `update_list_item` is `SECURITY INVOKER`; auth safety comes from RLS and `auth.uid()` membership checks, not definer privileges.
- Active offer filtering must stay null-safe and match public visibility windows: `valid_from IS NULL OR valid_from <= today`, `valid_to IS NULL OR valid_to >= today`.
- `/optimize` must verify caller membership on `body.list_id` before loading any list items or returning shopping intent data.

## Shopping Lists

- Shopping lists are multi-list: `GET /lists` returns owned + shared summaries, `POST /lists` creates non-default owned lists, `POST /lists/select` sets current `user_profiles.active_list_id`, and `GET /lists/active` stays compatibility alias for selected list detail.
- Default list is protected: owner may share it but may never rename or delete it. Non-default owned lists may be renamed/deleted only by owner; shared members cannot rename/delete owner lists. When owner deletes shared non-default list, active members must receive `app_notifications` + Web Push (if subscribed) and their `active_list_id` must fall back to default list.
- `POST /lists/{id}/reset` clears current list items after frontend confirmation and requires list membership.
- `POST /lists/{id}/items`, `DELETE /lists/{id}/items/{item_id}`, `POST /lists/{id}/items/{item_id}/toggle`, `POST /lists/{id}/items/{item_id}/check`, and purchase flows tied to `list_id` must verify list membership before any read/write using the service-role client.
- Use RPC helpers for concurrent-safe item mutation (`update_list_item`, `append_list_item`, `remove_list_item`) instead of overwriting full `shopping_lists.items` arrays. These RPCs are `SECURITY INVOKER` and must keep `search_path = public` pinned.
- Direct sharing flow is email-targeted: `POST /lists/{list_id}/invites` resolves an already-registered auth user, creates `list_invites` + `app_notifications`, and recipient accepts/declines via `/lists/invites/{invite_id}/accept|decline`. `DELETE /lists/{list_id}` on shared lists also emits `app_notifications` to active members with payload redirecting to `/lista`.

## Push Favorites Webhook

- `POST /push/notify-favorites` must ignore offers that are draft/unconfirmed, outside current validity window, missing a flyer, or linked to a non-public / non-done flyer. Favorite notifications are only for publicly visible offers.

## Ignore Rules

- Track `.env.example` and `.env.test.example`.
- Ignore `.env`, `.env.local`, `.env.test`, Python caches, coverage artifacts, editor files, macOS junk, and Supabase local state.
