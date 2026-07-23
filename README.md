# GiroSpesa — Backend

> Scope: questo file e' documentazione per persone. Regole operative per agenti e convenzioni di esecuzione vivono in `AGENTS.md`.

FastAPI backend per GiroSpesa. Contiene la logica di business dell'applicazione: ottimizzazione lista della spesa, gestione liste condivise, geocoding indirizzi, push notification, analytics B2B e catalogo prodotti.

> **Estrazione AI volantini:** questo repo contiene runtime, review flow e CLI di valutazione. Il vecchio workspace di estrazione e' stato assorbito in questo backend.

## System Overview

Il frontend web usa Supabase Auth con `@supabase/ssr`: browser, proxy Next.js e Server Components condividono la sessione tramite cookie SSR di Supabase. Le chiamate autenticate a questo backend inoltrano sempre il token accesso Supabase in `Authorization: Bearer <token>`.

Il backend valida i bearer token utente tramite signing keys/JWKS di Supabase e usa `SUPABASE_SECRET_KEY` per operazioni server-side privilegiate su Auth, Database e Storage. Non esiste un cookie sessione backend per auth applicativa o stream SSE.

Le notifiche di pubblicazione volantino/offerte preferite sono accodate in `notification_jobs` e drenate fuori dalle richieste utente da APScheduler o da `POST /ops/cron/notifications`.
`GET /products` supporta `favorites_only=true` per utenti autenticati: il filtro sui prodotti preferiti avviene lato backend prima di paginazione, conteggi e filtro supermercato.

Dettagli architetturali: [docs/architecture.md](docs/architecture.md).

## Quick Start

```bash
cp .env.example .env
cp .env.test.example .env.test
supabase start
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.seed_admin
python -m uvicorn main:app --reload --port 8000
```

- API locale: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Frontend separato: `../girospesa-webapp/`, porta `3000`

Guida completa: [docs/local-development.md](docs/local-development.md).

## Documentation

| Tema | File |
|------|------|
| Architettura e struttura repo | [docs/architecture.md](docs/architecture.md) |
| API, autenticazione e contratti endpoint | [docs/api.md](docs/api.md) |
| Flussi principali | [docs/flows.md](docs/flows.md) |
| Sviluppo locale | [docs/local-development.md](docs/local-development.md) |
| Test e snapshot | [docs/testing.md](docs/testing.md) |
| Variabili env, servizi esterni e logging | [docs/configuration.md](docs/configuration.md) |
| Schema, RLS, Storage e Analytics B2B | [docs/data-model.md](docs/data-model.md) |
| Scheduled jobs e cleanup | [docs/jobs.md](docs/jobs.md) |
| Deploy produzione backend | [docs/deploy-production.md](docs/deploy-production.md) |
| Deploy Render locale senza GitHub Actions | [docs/deploy-render-local.md](docs/deploy-render-local.md) |

Use the workspace guide [../docs/deploy-production-guide.md](../docs/deploy-production-guide.md) only when coordinating multiple systems together: backend, frontend, DNS, Supabase, mobile apps, stores, and UAT.

## Common Commands

```bash
.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance
.venv/bin/python -m pytest tests/integration -v
RUN_PERFORMANCE_TESTS=1 .venv/bin/python -m pytest tests/performance -v -s
.venv/bin/python -m scripts.seed_admin --check
```

## Project Map

```text
girospesa-backend/
├── main.py
├── api/routers/
├── core/
├── services/
├── scripts/
├── supabase/migrations/
├── tests/
└── docs/
```
