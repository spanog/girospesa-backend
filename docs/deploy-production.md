# Backend Production Deploy

This file contains the backend-only production deployment runbook. Use the root workspace guide only when coordinating backend, frontend, DNS, Supabase, app stores, and UAT together.

## Overview

The backend deploys to Render from GitHub.

- `render.yaml` describes the FastAPI web service.
- `.github/workflows/ci.yml` runs backend CI only when started manually with `workflow_dispatch`, so pushes to `main` do not consume GitHub Actions minutes.
- `.github/workflows/render-keepalive.yml` pings `/health` every 5 minutes.
- `.github/workflows/daily-maintenance.yml` calls daily maintenance remotely.
- `.github/workflows/supabase-db-production.yml` applies Supabase migrations when `supabase/**` changes on `main`.
- Render service auto-deploy is set to `On Commit` (`autoDeployTrigger: commit`) so it does not wait for GitHub checks.

## Quick Rules

- Code changes on `main` trigger Render auto-deploy.
- Supabase migration changes use workflow `Supabase DB Production`.
- Render env changes require manual deploy/redeploy, then `/health` check.
- `OPS_CRON_SECRET` must match in Render and GitHub Secrets.
- `VAPID_PUBLIC_KEY` also affects frontend Vercel/GitHub mobile builds.

## Deploy From Scratch

1. Create or verify the production Supabase project.
2. Collect `SUPABASE_URL`, service role key, database password, project ref, and Supavisor session-mode connection string.
3. In Render, create a `Web Service` connected to `spanog/girospesa-backend`.
4. Keep root directory at the backend repo root.
5. Verify Render reads `render.yaml`.
6. Set production branch to `main`.
7. Enable `Auto-Deploy`.
8. Add all required Render env vars.
9. Start first deploy.
10. Verify `https://<render-service>.onrender.com/health`.
11. Configure custom domain `api.girospesa.it` in Render.
12. Configure Cloudflare DNS.
13. Set `BACKEND_URL=https://api.girospesa.it`.
14. Redeploy Render.
15. Verify `https://api.girospesa.it/health`.

## Redeploy

Code:

1. Open PR.
2. Run local backend tests, or start GitHub Actions `Backend CI` manually only when PR-grade remote validation is needed.
3. Merge to `main`.
4. Render auto-deploys from the pushed `main` commit.
5. Check `/health` and startup logs.

Environment:

1. Update env in Render.
2. Run manual deploy/redeploy.
3. Check `/health`.
4. Verify the endpoint affected by the changed env.
5. Update [../../docs/deploy-production-status.md](../../docs/deploy-production-status.md) when production state changed.

Migrations:

1. Commit migration under `supabase/**`.
2. Merge to `main`.
3. Verify GitHub workflow `Supabase DB Production`.
4. Run it manually if needed.
5. Verify the endpoint using the changed schema.

## Required Render Env

Set these in Render Dashboard: service `girospesa-backend` → `Environment`.

| Variable | Required | Meaning | Value / source |
|---|---:|---|---|
| `PYTHON_VERSION` | Yes | Python runtime used by Render build/runtime. | `3.14.3`, kept aligned with `.python-version`, `pyproject.toml`, and `render.yaml`. |
| `ENVIRONMENT` | Yes | Enables production logging and production CORS behavior. | `production`, fixed in `render.yaml`. |
| `FRONTEND_URL` | Yes | Public frontend origin and auth redirect destination. | Production frontend URL, currently `https://www.girospesa.it`; get from Vercel/custom domain setup. |
| `BACKEND_URL` | Yes | Public backend base URL used for auth callback links and deep links. | Render custom domain, currently `https://api.girospesa.it`; get after Render custom domain + DNS are configured. |
| `CORS_EXTRA_ORIGINS` | Mobile/prod app | Extra allowed origins beyond `FRONTEND_URL`. | Comma-separated app origins, currently `https://app.girospesa.local,capacitor://app.girospesa.local`; get from Capacitor/mobile app config. |
| `SUPABASE_URL` | Yes | Supabase project API URL used by backend clients and storage URL building. | Supabase Dashboard → Project Settings → API → Project URL, format `https://<project-ref>.supabase.co`. |
| `SUPABASE_SECRET_KEY` | Yes | Supabase service-role/secret key for privileged server-side Auth, Database, and Storage access. | Supabase Dashboard → Project Settings → API → service role key / secret key. Never expose to frontend. |
| `APP_SESSION_SECRET` | Yes | HS256 secret for backend recovery/session tokens. Must match frontend when frontend validates same backend session token. | Generate once with `openssl rand -hex 32`; store same value anywhere else that validates backend session tokens. |
| `DB_DSN` | Yes | Direct Postgres connection for LISTEN/NOTIFY, concurrent-safe list sync, and direct DB operations. | Supabase Dashboard → Connect → URI / Supavisor session-mode connection string; use production DB password. |
| `GOOGLE_API_KEY` | Flyer extraction | Google AI Studio/Gemini API key for AI flyer extraction. | Google AI Studio → API keys. Required when production extraction is enabled. |
| `GEMINI_MODEL` | Flyer extraction | Gemini model name used by extraction runtime. | `gemma-4-31b-it`, fixed in `render.yaml` unless model is intentionally changed. |
| `GEOCODING_PROVIDER` | Yes | Enables address geocoding provider. | `nominatim`, fixed in `render.yaml` for production behavior. |
| `VAPID_PRIVATE_KEY` | Web Push | Private VAPID key used to sign browser push notifications. | Generate VAPID keypair with `pywebpush`/`web-push`; keep private key only in Render. |
| `VAPID_PUBLIC_KEY` | Web Push | Public VAPID key used by frontend/browser subscription. | Same generated VAPID keypair; also copy to frontend/mobile config where subscription is created. |
| `VAPID_MAILTO` | Web Push | Contact claim sent with VAPID requests. | `mailto:info@girospesa.it`, fixed in `render.yaml`. |
| `WEBHOOK_SECRET` | Push webhook | Shared secret for Supabase Database Webhook → `POST /push/notify-favorites`. | Generate once with `openssl rand -hex 32`; copy same value into Supabase webhook header `X-Webhook-Secret`. |
| `OPS_CRON_SECRET` | Scheduled maintenance | Shared secret for GitHub Actions daily maintenance → `POST /ops/cron/daily-maintenance`. | Generate once with `openssl rand -hex 32`; copy same value to GitHub Secret `OPS_CRON_SECRET`. |

Optional production integrations:

| Variable | Required | Meaning | Value / source |
|---|---:|---|---|
| `FCM_ENABLED` | Native push | Enables native Firebase Cloud Messaging delivery. | `true` only after Firebase service account values are configured; otherwise `false`. |
| `FCM_PROJECT_ID` | Native push | Firebase project id used in FCM v1 endpoint. | Firebase Console → Project settings → General → Project ID. |
| `FCM_CLIENT_EMAIL` | Native push | Service account email used to mint OAuth token for FCM. | Firebase Console / Google Cloud IAM → service account JSON → `client_email`. |
| `FCM_PRIVATE_KEY` | Native push | Service account private key used to sign OAuth JWT for FCM. | Firebase service account JSON → `private_key`; store with escaped newlines (`\n`) if Render input is single-line. |
| `SMTP_HOST` | Contact forms | SMTP relay host for `/contact-requests`. | Brevo SMTP settings, currently `smtp-relay.brevo.com`. |
| `SMTP_PORT` | Contact forms | SMTP relay port. | Brevo SMTP settings, currently `2525`. |
| `SMTP_USERNAME` | Contact forms | SMTP login username. | Brevo SMTP settings → SMTP login. |
| `SMTP_PASSWORD` | Contact forms | SMTP relay password/API key. | Brevo SMTP settings → SMTP key/password. |
| `SMTP_USE_TLS` | Contact forms | Enables STARTTLS on plain SMTP connection. | `false` for current Brevo `2525` setup. |
| `SMTP_USE_SSL` | Contact forms | Enables implicit SSL SMTP connection. | `false` for current Brevo `2525` setup. |
| `MAIL_FROM` | Contact forms | Sender address for outgoing contact emails. | `info@girospesa.it`; must be allowed/verified in SMTP provider. |
| `WEBMASTER_EMAIL` | Contact forms | Destination inbox for public contact forms. | GiroSpesa mailbox, normally `info@girospesa.it` or webmaster mailbox hosted by Aruba. |
| `ADMIN_EMAIL` | Admin seed | Admin account email created/verified by `scripts.seed_admin`. | Chosen production admin email. Store in Render before running seed. |
| `ADMIN_PASSWORD` | Admin seed | Admin account password used by `scripts.seed_admin`. | Generate/store securely in password manager; rotate after initial seed if needed. |

## GitHub Actions Secrets

Configure in `spanog/girospesa-backend`.

| Secret | Purpose |
|---|---|
| `BACKEND_DAILY_MAINTENANCE_URL` | Daily maintenance workflow |
| `BACKEND_HEALTHCHECK_URL` | Render keepalive workflow |
| `OPS_CRON_SECRET` | Authenticates daily maintenance |
| `SUPABASE_ACCESS_TOKEN` | Supabase migration workflow |
| `SUPABASE_DB_PASSWORD` | Supabase migration workflow |
| `SUPABASE_PROJECT_ID` | Supabase migration workflow |

Verified on 2026-07-16. No backend GitHub Actions variables were configured at that time.

Example:

```text
BACKEND_HEALTHCHECK_URL=https://api.girospesa.it/health
BACKEND_DAILY_MAINTENANCE_URL=https://api.girospesa.it/ops/cron/daily-maintenance
```

## Database Deploy Notes

- Render backend deploy does not apply Supabase migrations.
- Production schema updates through `.github/workflows/supabase-db-production.yml`.
- Workflow runs on push to `main` when `supabase/**` changes.
- Workflow can also be run manually with `workflow_dispatch`.
- First setup: run the workflow manually once after configuring secrets.

## Smoke Tests

Minimum:

```bash
curl -fsS https://api.girospesa.it/health
curl -fsS "https://api.girospesa.it/products?lat=38.4116708&lng=16.0742832&max_distance_km=10" >/dev/null
```

If Supabase changed, verify frontend login and one authenticated route.
If SMTP changed, submit `/contact-requests`.
If FCM changed, verify `POST /push/native/subscribe` and banner delivery on device.
If Gemini changed, run extraction on a test flyer.

## Render Free Notes

Render Free is not designed for stable production. It can spin down after inactivity and restart unexpectedly. The backend keeps local APScheduler, but production cleanup is also called by GitHub Actions through `POST /ops/cron/daily-maintenance`.

`render-keepalive.yml` pings `BACKEND_HEALTHCHECK_URL` every 5 minutes with retries and timeout. This mitigates cold starts, but it is not equivalent to a paid plan.

`daily-maintenance.yml` uses `curl --fail-with-body`, so HTTP failures preserve response bodies in GitHub logs. `/ops/cron/daily-maintenance` is best-effort: if one internal step fails, response reports `status=partial_error` and failed step names in `errors`, while other cleanup steps continue.
