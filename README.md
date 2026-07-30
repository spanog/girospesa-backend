# GiroSpesa — Backend

> Scope: questo file e' documentazione per persone. Regole operative per agenti e convenzioni di esecuzione vivono in `AGENTS.md`.

FastAPI backend per GiroSpesa. Contiene la logica di business dell'applicazione: offerte estratte dai volantini, gestione liste condivise, geocoding indirizzi, notifiche di pubblicazione e analytics B2B.

> **Estrazione AI volantini:** questo repo contiene runtime, review flow e CLI di valutazione. Il vecchio workspace di estrazione e' stato assorbito in questo backend. Quando il modello localizza un packshot nel PDF, la bozza conserva automaticamente il relativo ritaglio per la review. I PDF nuovi vengono inviati a Gemini in chunk da due pagine, generati uno alla volta; i crop sono renderizzati a risoluzione 2× e ogni pagina viene renderizzata una sola volta per tutti i suoi prodotti. La localizzazione del crop è persistita internamente, così dopo un riavvio vengono completate solo le immagini mancanti. I checkpoint conservano la dimensione chunk originale per una ripresa coerente.

## System Overview

Il frontend web usa Supabase Auth con `@supabase/ssr`: browser, proxy Next.js e Server Components condividono la sessione tramite cookie SSR di Supabase. Le chiamate autenticate a questo backend inoltrano sempre il token accesso Supabase in `Authorization: Bearer <token>`.

Il backend valida i bearer token utente tramite signing keys/JWKS di Supabase e usa `SUPABASE_SECRET_KEY` per operazioni server-side privilegiate su Auth, Database e Storage. Non esiste un cookie sessione backend per auth applicativa o stream SSE; i guest ricevono soltanto un cookie tecnico firmato e `HttpOnly` per mantenere il filtro geografico delle discovery pubbliche.

Le notifiche di pubblicazione volantino sono accodate in `notification_jobs` e drenate fuori dalle richieste utente da APScheduler o da `POST /ops/cron/notifications`.
`GET /offers` restituisce offerte pubbliche autosufficienti, incluse immagine estratta, dati del supermercato e periodo di validità. Con la posizione attiva restituisce solo offerte dei supermercati nel raggio richiesto; quando una stessa offerta di un volantino è pubblicata per più sedi, restituisce una sola copia, associata alla sede più vicina. Il filtro ripetibile `supermarket_ids` limita prima le sedi candidate. `GET /supermarkets?with_active_offers=true` e `GET /flyers/public` applicano il raggio: per utenti autenticati deriva posizione e distanza dal profilo, per ospiti richiedono coordinate esplicite. I volantini restituiti restano scaricabili; la loro anteprima è una WebP privata generata dal backend e consegnata tramite URL firmato.

In locale, impostare anche `SUPABASE_JWT_SECRET` uguale al `JWT_SECRET` di Docker: consente al backend di verificare i token HS256 emessi dall'istanza Supabase locale. In produzione il backend continua a verificare i token ES256/RS256 tramite JWKS.
L'aggiunta ripetuta della stessa offerta attiva a una lista incrementa la quantità della riga esistente in modo atomico.

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

## Baseline catalogo prodotti

Il tag annotato `v.0.1-product-catalog` conserva l'architettura precedente basata su prodotti canonici, preferiti e immagini di catalogo. Il modello corrente è basato esclusivamente sulle offerte e sulle immagini estratte dal volantino. Il tag è il punto di ripartenza per una futura reintroduzione del catalogo, delle notifiche sui preferiti e di immagini curate ad alta qualità.

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
| Assessment sicurezza e follow-up staging | [docs/security-assessment-2026-07-29.md](docs/security-assessment-2026-07-29.md) |

Use the workspace guide [../docs/deploy-production-guide.md](../docs/deploy-production-guide.md) only when coordinating multiple systems together: backend, frontend, DNS, Supabase, mobile apps, stores, and UAT.

## Common Commands

```bash
.venv/bin/python -m pytest tests -v --ignore=tests/integration --ignore=tests/performance
.venv/bin/python -m pytest tests/integration -v
RUN_PERFORMANCE_TESTS=1 .venv/bin/python -m pytest tests/performance -v -s
.venv/bin/python -m scripts.seed_admin --check
```

## Reset iniziale modello solo offerte

La migrazione `20260724000000_offer_only_reset.sql` è intenzionalmente distruttiva: elimina volantini, offerte, catalogo e preferiti, mantenendo account, supermercati, avatar, loghi e liste normalizzate come voci manuali. Prima applicare la migrazione, eseguire una sola volta il comando seguente con credenziali service-role: svuota esclusivamente i bucket `flyers` e `product-images`, verifica che siano vuoti e non tocca `avatars` o `logos`.

```bash
.venv/bin/python -m scripts.reset_offer_only_storage --confirm-offer-only-reset
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
