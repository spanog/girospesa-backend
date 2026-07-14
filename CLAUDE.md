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
- No application endpoint may trust client-supplied `admin`, `manager`, `role`, or similar flags in query params, headers, or request bodies to determine privileges or data scope. Authorization must derive only from validated auth context server-side.
- Auth BFF errors must not collapse into opaque `"failed"` responses. For signup, log upstream exception with stack trace server-side, but return only sanitized user-facing copy. Duplicate-email flow must not confirm account existence; use neutral text like `Registrazione non riuscita. Verifica i dati inseriti oppure accedi se hai già un account.`.
- Public contact flows (`/contact-requests`) are mail-first: do not reintroduce app tables or client-side inserts for bug reports, collaboration requests, or missing-flyer requests. Bug reports must not depend on Supabase Storage; screenshots are optional and, when present, travel as direct `image/png` or `image/jpeg` email attachments.

## Integration Test Isolation

- `tests/integration/` must boot a fresh Docker stack on project `girospesa-itest` and destroy only that stack with `down -v --remove-orphans`.
- Integration env overrides (`SUPABASE_URL`, `DB_DSN`, keys, etc.) must stay scoped to the `pytest` session and be restored afterward. Never mutate process env at module import time.
- Dev stack (`supabase start`, backend on `.env`, local ports `54321+`) must remain untouched by integration test setup/teardown.

## Testing Strategy

- Router and service changes must add or update tests in the closest layer first: unit/service for pure logic, integration for real DB/API contracts.
- Contract snapshots are required for stable JSON payloads touched by the change. Store them under `tests/__snapshots__/` or `tests/integration/__snapshots__/`.
- Normalize unstable values before snapshot compare: UUID, invite token, ISO timestamp, variable URL host/query.
- Keep explicit assertions for permissions, sorting, filtering, lifecycle transitions, and domain invariants even when a snapshot exists.

## Git

- Keep `main` clean for deploy-ready code.
- Push ongoing V1 work to a dedicated long-lived branch.
- Never commit local secrets or machine-specific env files.

## Flyer Review

- Manual draft-offer create/update endpoints on `/flyers/{flyer_id}/draft-offers` own draft product fields on `offers`: `draft_name`, `draft_brand`, `draft_category`, `draft_subcategory`. Format fields (`format`, `format_key`, `format_label`) belong to the offer, not the product.
- In flyer review, offer validity is flyer-scoped: manual draft creation inherits `flyers.valid_from`/`flyers.valid_to`, per-offer PATCH must not own dates, and `PATCH /flyers/{flyer_id}` propagates validity changes to every offer linked to that source flyer, including published target clones.
- Draft review can also stage `draft_image_url` on `offers`. Review responses expose `image_url` with precedence `draft_image_url -> products.image_url`, so unbound drafts can preview a product image before the canonical product exists.
- Draft offers may have `product_id = NULL`. `product_id` is required only when `is_confirmed = true`. A draft bound to an existing product exposes `binding_status='existing'` plus `linked_product`; an unbound draft exposes `binding_status='new_on_confirm'`.
- Detaching a draft offer sets `product_id = NULL` only. It must not create a product. On `POST /flyers/{flyer_id}/offers/confirm`, every unbound draft creates/upserts the canonical product from current draft fields, then the offer is confirmed.
- Flyer upload/review is now multi-supermarket. `flyers` rows with `flyer_kind='source'` own one extraction/review flow, while `flyer_targets` stores the selected supermarket branches. `POST /flyers/upload` accepts repeated `supermarket_ids`, `GET|PUT|PATCH /flyers/{flyer_id}/targets` manages targets before publication, and `POST /flyers/{flyer_id}/offers/confirm` must materialize one published flyer plus one confirmed-offer set per target supermarket.
- Source-flyer offers are authoring masters, not public offers. Persist `offers.offer_kind='source_master'` on source rows and `offers.offer_kind='published_target'` on derived public clones. Every published clone must set `source_offer_id` back to its source-master row.
- Public/customer-facing offer reads (`/products`, `/favorites`, `/optimize`, authenticated browsing via RLS) must only treat `offer_kind='published_target'` as a real offer. Source-master rows stay visible only in source-flyer admin review.
- Post-publication edits and deletes still happen from the source flyer. Updating or deleting a confirmed source-master row must propagate to all linked clones through `source_offer_id`.
- `GET /flyers` and `GET /flyers/{id}` for source flyers must expose `draft_count`, `confirmed_count`, and `published_target_count` so admin UI can distinguish "ancora da confermare" from "già pubblicato" without abusing `is_public` on the source flyer.
- Nightly cleanup must preserve expired `flyers` rows and files for admin history. Only linked `offers` are removed once `flyers.valid_to < today`.
- Supermarket managers can belong to multiple branches through `manager_supermarkets`. Auth/session payloads should expose `managed_supermarket_ids`; `managed_supermarket_id` remains fallback-only for older rows/tests.
- `POST /flyers/{flyer_id}/draft-offers/{offer_id}/image` accepts `multipart/form-data` only for `binding_status='new_on_confirm'` drafts. Bound or already-confirmed offers must not mutate catalog images from flyer review.
- `format` must be structured `ProductFormat` JSON. Plain text format is forbidden.
- **Canonical product identity is `name + brand`** — products are uniquely keyed on `UNIQUE NULLS NOT DISTINCT (name, brand)`. Format is an attribute of the offer, not the product.
- `format_key` and `format_label` are always derived backend-side from normalized `format` and stored on the `offers` row.
- Persist `offers.format` in compact canonical form only: omit `null`, empty arrays, and default `false` flags when not semantically needed.
- LLM/provider output should keep `format` sparse too: emit only `tipo` plus relevant fields. Backend canonicalization stays authoritative.
- Extraction-only `format.varianti` must be exploded before persistence. Each variant produces a separate offer row under the same canonical product.
- Extraction pipeline should normalize each product once, dedupe on `(name, brand)` before persistence, fuzzy-match existing products by brand/name, and batch-upsert draft offers. It must not create new canonical products during extraction.
- Draft offer persistence must stay idempotent: dedupe pending offer rows on `(flyer_id, draft_product_key, format_key)`, then use `offers.upsert(..., on_conflict="flyer_id,draft_product_key,format_key", ignore_duplicates=True)`.
- For multi-page PDFs, Gemini extraction must split the document into rigid 3-page PDF chunks and process one chunk per request. After each successful chunk, persist draft offers immediately and update `flyers.extraction_metadata` with `pages_processed`, `current_chunk_start`, `current_chunk_end`, `progress_percent`, `chunks_completed`, `chunks_total`, `products_found`, `last_completed_chunk`, and `next_chunk_*`. If one chunk fails after retries, set flyer `status='error'`, keep already saved draft offers, and expose `resume_available` plus `failed_chunk_*` / `next_chunk_*` metadata so the same `POST /flyers/{flyer_id}/extract` call can resume from the first failed chunk. Resume detection must read persisted `extraction_metadata`, because router flips flyer back to `processing` before background task restarts.
- Gemini retry/failure logs must preserve structured provider context when available: exception type, `code`, `status`, `message`, HTTP status/body, and request id. Keep same formatted string in app logs and `retry_errors` sent to `extraction_log`.
- Before upserting, pipeline runs a fuzzy pre-check per brand bucket: `_find_similar_product()` in `ExtractionService` uses `rapidfuzz.fuzz.partial_ratio` on names (≥0.85). Brand is used as an exact-match bucket for candidate retrieval; name fuzzy match determines whether to reuse an existing `product_id`. Thresholds configurable via `product_name_similarity_threshold` / `product_brand_similarity_threshold` in `core/config.py`.
- Draft and confirmed offer payloads returned by flyer review endpoints must flatten `products.subcategory` alongside `category`.
- Draft and confirmed offer payloads must expose both `format` and `format_label` (sourced from the offer row, not the product).
- `GET /products` ordina default per `products.name`; `sort=expiry` ordina per `offers.valid_to` crescente con offerte senza scadenza dopo, poi per `products.name`.
- `GET /products` accetta ancora `supermarket=<slug>` per compatibilita', ma i filtri punto-vendita customer-facing devono preferire `supermarket_id=<id>` per distinguere filiali della stessa insegna.
- `GET /products` accetta anche `product_id=<canonical-products.id>` per filtrare esattamente tutte le offerte pubbliche attive di un singolo prodotto canonico; il filtro deve convivere con distanza, supermercato e sort.
- `GET /products` search RPC `public.search_products_catalog` must preserve fuzzy `word_similarity` ranking but also match prefix/substring queries on product name and brand, so inputs like `mozza` still return `Mozzarella`.
- `flyers.extraction_metadata` should keep live extraction progress during `processing`, then per-stage timing keys (`provider_seconds`, `variant_expansion_seconds`, `normalization_seconds`, `dedupe_seconds`, `product_upsert_seconds`, `offer_insert_seconds`, `total_seconds`) plus product-count and average-format-size telemetry at completion.
- Extraction completion/failure Web Push payloads must include structured `data`: `kind`, `flyer_id`, `status`, `products_count`, and `url`. Frontend admin cache sync depends on those fields.
- Admin product delete on `/admin/products/{id}` is hard delete only when product has zero linked offers; endpoint must also delete linked favorites.
- `purchase_history.product_id` is historical snapshot data, not live FK protection for canonical products.
- `purchase_history.quantity` stores purchased quantity; stored `price_paid`, `price_original`, and `savings` must be quantity-scaled totals, not unit values.
- `purchase_history` must also snapshot `brand`, `format_label`, `image_url`, `category`, `subcategory`, and unit-price fields so `/purchases/history` keeps rich cards even if linked offers/products later change or disappear.
- `GET /purchases/history` must support incremental loading via stable cursor pagination (`cursor_purchased_at` + `cursor_id`) plus `limit`, together with server-side filters `category`, `subcategory`, `supermarket`, and `source`. Response must include `next_cursor_purchased_at` / `next_cursor_id` / `has_more` and aggregate totals computed over the full filtered result set, not just the current page.
- Purchase and undo flows on `/purchases/items/{item_id}` must mutate `shopping_lists.items` through `update_list_item` RPC patches, never with full-array `shopping_lists.update(...)` writes.

## Supabase Query Builder

- Match pinned client API exactly. Example: PostgREST `.order()` expects `nullsfirst`, not `nulls_first`.
- Local Supabase API exposure stays limited to `public` schema. `pg_graphql` is disabled; do not build or document `/graphql/v1` flows.
- RLS-only helper functions that need `SECURITY DEFINER` privileges must live in a non-exposed schema such as `private`; do not publish them from `public` or document them as client-callable RPCs.
- Public Storage buckets (`avatars`, `logos`, `product-images`) rely on signed-less `/storage/v1/object/public/...` URLs only. Do not depend on anonymous bucket listing via `storage.objects` policies.

## Admin Seed

- `scripts.seed_admin` must be idempotent and must ensure the admin has `app_metadata.role = "admin"`, `public.user_profiles.role = 'admin'`, address `Via Palmiro Togliatti, 89024 Polistena (RC)`, populated `home_lat/home_lng` for that address, and one empty owner shopping list named `La mia lista`.
- New auth users must get one empty owner shopping list named `La mia lista` from the DB signup trigger. The same trigger copies signup `raw_user_meta_data` address fields (`home_address`, `home_city`, `home_province`, `home_postal_code`) into `user_profiles`; keep this in sync with shared Supabase migrations.

## Optimizer

- `/optimize` must resolve `pinned_offer_id` before fuzzy matching. A list item added from an offer is an exact offer match (`match_score=1.0`) when the offer is active and in range. `PATCH /lists/{list_id}/items/{item_id}` must persist any explicit `pinned_offer_id`, `pinned_product_id`, `found_deals`, category, and subcategory updates through RPC `update_list_item`, then reread the saved list item so lista, giro spesa, acquisti, and freshness stay aligned. `update_list_item` is `SECURITY INVOKER`; auth safety comes from RLS and `auth.uid()` membership checks, not definer privileges.
- Shopping-list snapshots for items linked to offers/products must preserve `brand` alongside `name`, `pinned_product_id`, `pinned_offer_id`, and taxonomy fields. Read paths such as `GET /lists/active` should also backfill missing `brand` from the linked product/offer so older snapshots still render branded rows correctly.
- Shared-list read paths are viewer-specific. Keep stored `pinned_offer_id` and `found_deals` unchanged in `shopping_lists.items`, but when current member cannot see that supermarket inside their own `search_*`/`home_*` + `max_distance_km` context, `GET /lists/active` and `GET /lists/{id}` must mask the item as manual/no-offer and return `offer_visibility_status='hidden_for_viewer'`. Do not leak supermarket or price through masked list responses.
- Active offer filtering must stay null-safe and match public visibility windows: `valid_from IS NULL OR valid_from <= today`, `valid_to IS NULL OR valid_to >= today`.
- `/optimize` must verify caller membership on `body.list_id` before loading any list items or returning shopping intent data.
- `/optimize` and `GET /lists/{id}/deal-freshness` must use the viewer’s visible supermarket subset, not the owner’s. A hidden pinned offer must surface as `unavailable` for freshness, must not trigger stale-offer cleanup, and must not appear in optimizer store groups or alternatives.

## Shopping Lists

- Shopping lists are single-owner-list for MVP, but shared workspaces remain selectable. Every account owns exactly one protected owner list; `GET /lists` returns the owner list plus any shared lists visible to the user, includes `owner_display_name` for selector labeling, and `POST /lists/select` persists which visible list is currently active via `user_profiles.active_list_id`.
- `GET /lists/active` must resolve the current workspace in this order: explicit active shared/owner list if still visible, fallback owner list, fallback first visible shared list. If no owner list exists yet, create it and persist it as active.
- Owner list is protected: no create-secondary-owner-list flow remains. Shared members cannot rename/delete owner lists.
- When owner removes one member from a shared list via `DELETE /lists/{list_id}/members/{user_id}`, target membership must be deleted and only removed user must receive `app_notifications` + Web Push with redirect payload to `/lista`.
- Same `DELETE /lists/{list_id}/members/{user_id}` endpoint also allows a shared `member` to remove only themselves from that list. Self-leave must delete membership and notify only list owner via `app_notifications` + Web Push with redirect payload to `/lista`.
- Shared-list notifications `list_member_removed` and `list_member_left` must include actor identity in human-readable form `Nome Cognome (email)` when email is available, while preserving structured payload keys for display name and email separately.
- `GET /lists/{list_id}/members` must return member rows already flattened for frontend consumption: top-level `display_name`, `avatar_url`, and `email`, without requiring client-side unpacking of nested profile objects.
- Sync live lista condivisa passa da `GET /lists/{list_id}/events` (`text/event-stream`): backend pubblica `pg_notify` con payload JSON minimale (`list_id`, `event`, `reason`, `changed_at`, `id`) dopo mutazioni lista, membership o invite. Non introdurre broadcaster in-memory per questo dominio.
- `POST /lists/{id}/reset` clears current list items after frontend confirmation and requires list membership.
- `POST /lists/{id}/items/remove-purchased` is the dedicated bulk action for clearing purchased list items while preserving `purchase_history`; keep single-item delete on purchased rows blocked.
- `POST /lists/{id}/clear-stale-offers` must clear expired/unavailable snapshots through `update_list_item` RPC using the SQL argument name `p_patch`, not ad-hoc payload keys, so frontend auto-cleanup can persist without 500s.
- `POST /lists/{id}/items`, `DELETE /lists/{id}/items/{item_id}`, `POST /lists/{id}/items/{item_id}/toggle`, `POST /lists/{id}/items/{item_id}/check`, and purchase flows tied to `list_id` must verify list membership before any read/write using the service-role client.
- Use RPC helpers for concurrent-safe item mutation (`update_list_item`, `append_list_item`, `remove_list_item`) instead of overwriting full `shopping_lists.items` arrays. These RPCs are `SECURITY INVOKER` and must keep `search_path = public` pinned.
- When backend creates an owned list through direct Postgres right after `auth.admin.create_user`, guard against short auth/db propagation lag: wait for `auth.users` row visibility before retrying the insert instead of failing the request with an FK race.
- Direct sharing flow is email-targeted: `POST /lists/{list_id}/invites` resolves an already-registered auth user, creates `list_invites` + `app_notifications`, and recipient accepts/declines via `/lists/invites/{invite_id}/accept|decline`. Invite create/list/revoke and legacy token-share create are owner-only; shared members may not manage invites. `DELETE /lists/{list_id}` on shared lists also emits `app_notifications` to active members with payload redirecting to `/lista`.
- Accepting direct share invite via `POST /lists/invites/{invite_id}/accept` must create membership, update invite state, and set that shared list as the recipient's active workspace.
- Revoking a pending direct share invite must not erase inbox history. `DELETE /lists/{list_id}/invites/{invite_id}` sets invite `status='revoked'` and updates existing `app_notifications` row with `data.invite_status='revoked'` plus `revoked_at`. Subsequent `/lists/invites/{invite_id}/accept|decline` calls for that recipient must return explicit `409 Invite has been revoked`, not `404`.

## Push Favorites Webhook

- `POST /push/notify-favorites` must ignore offers that are draft/unconfirmed, outside current validity window, missing a flyer, or linked to a non-public / non-done flyer. Favorite notifications are only for publicly visible offers. For each eligible recipient, persist an `app_notifications` row with kind `favorite_offer` before attempting push delivery, so inbox history survives closed clients, missing subscriptions, or missing browser/native delivery.
- Tutti gli eventi notificabili (`favorite_offer`, `flyer_published`, `list_invite`, `list_member_removed`, `list_member_left`, `extraction_complete`, `extraction_failed`) devono sempre persistere inbox notification quando l'evento e' valido. La consegna fisica Web Push/native FCM dipende solo da subscription/token presenti e permessi browser/dispositivo.
- Le notifiche customer `flyer_published` devono partire solo da `POST /flyers/{flyer_id}/offers/confirm`, esclusivamente alla prima pubblicazione del volantino (`flyers.is_public` prima `false`, dopo `true`) e solo per profili `customer` con supermercato entro `max_distance_km` usando `search_*` come priorita` rispetto a `home_*`.

## Ignore Rules

- Track `.env.example` and `.env.test.example`.
- Ignore `.env`, `.env.local`, `.env.test`, Python caches, coverage artifacts, editor files, macOS junk, and Supabase local state.
