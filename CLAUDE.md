# Project Conventions

## Commands

- Setup local env files: `cp .env.example .env && cp .env.test.example .env.test`
- Start Supabase local stack: `supabase start`
- Run app locally: `.venv/bin/python -m uvicorn main:app --reload --port 8000`
- Run unit-style tests: `.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance`
- Run integration tests: `.venv/bin/python -m pytest tests/integration -v`
- Run performance tests: `.venv/bin/python -m pytest tests/performance -v -s`

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
- Before upserting, pipeline runs a fuzzy pre-check per `format_key` bucket: `_find_similar_product()` in `ExtractionService` uses `rapidfuzz.fuzz.partial_ratio` on names (≥0.85) and `fuzz.ratio` on diacritic-normalized brands (≥0.90). Thresholds configurable via `product_name_similarity_threshold` / `product_brand_similarity_threshold` in `core/config.py`. Matches reuse the existing `product_id` — no duplicate row is created. `format_key` is always an exact-match gate; never fuzzified.
- Draft and confirmed offer payloads returned by flyer review endpoints must flatten `products.subcategory` alongside `category`.
- Draft and confirmed offer payloads must expose both `format` and `format_label`.
- `flyers.extraction_metadata` should keep per-stage timing keys (`provider_seconds`, `variant_expansion_seconds`, `normalization_seconds`, `dedupe_seconds`, `product_upsert_seconds`, `offer_insert_seconds`, `total_seconds`) plus product-count and average-format-size telemetry.
- Admin product delete on `/admin/products/{id}` is hard delete only when product has zero linked offers; endpoint must also delete linked favorites.
- `purchase_history.product_id` is historical snapshot data, not live FK protection for canonical products.

## Supabase Query Builder

- Match pinned client API exactly. Example: PostgREST `.order()` expects `nullsfirst`, not `nulls_first`.

## Admin Seed

- `scripts.seed_admin` must be idempotent and must ensure the admin has `app_metadata.role = "admin"`, `public.user_profiles.role = 'admin'`, address `Via Palmiro Togliatti, 89024 Polistena (RC)`, and one active empty owner shopping list.
- New auth users must get one active empty owner shopping list from the DB signup trigger; keep this in sync with webapp Supabase migrations.

## Optimizer

- `/optimize` must resolve `pinned_offer_id` before fuzzy matching. A list item added from an offer is an exact offer match (`match_score=1.0`) when the offer is active and in range.
- Active offer filtering must stay null-safe and match public visibility windows: `valid_from IS NULL OR valid_from <= today`, `valid_to IS NULL OR valid_to >= today`.

## Ignore Rules

- Track `.env.example` and `.env.test.example`.
- Ignore `.env`, `.env.local`, `.env.test`, Python caches, coverage artifacts, editor files, macOS junk, and Supabase local state.
