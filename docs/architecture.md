# Architecture

## System Context

```
┌──────────────────────────────────────────────────────────────────────┐
│                         GiroSpesa                            │
│                                                                      │
│  ┌────────────────────┐           ┌─────────────────────────────┐   │
│  │  lista-spesa-      │  REST API │  lista-spesa-               │   │
│  │  girospesa-webapp/     │ ────────▶ │  girospesa-backend/             │   │
│  │  (Next.js)         │           │  (questo repo — FastAPI)    │   │
│  │                    │           │                             │   │
│  │  Frontend SPA      │ ◀──────── │  /offers  /lists            │   │
│  │  Supabase Auth     │  JSON     │  /lists     /invite         │   │
│  │  Realtime subs     │           │  /push      /users          │   │
│  └────────────────────┘           │  /analytics /admin/...      │   │
│                                   └───────────┬─────────────────┘   │
│                                               │ service role key     │
│                                               ▼                      │
│                                   ┌───────────────────────┐         │
│                                   │       Supabase        │         │
│                                   │  PostgreSQL + Auth     │         │
│                                   │  Storage + RLS         │         │
│                                   └───────────────────────┘         │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  ExtractionService + review flow                          │     │
│  │  Upload flyer → extract → draft offers → confirm          │     │
│  │  Tutto gestito dentro questo backend                      │     │
│  └────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
```

Il frontend web usa Supabase Auth con `@supabase/ssr`: browser, proxy Next.js e Server Components condividono la sessione tramite i cookie SSR di Supabase, mentre le chiamate autenticate a questo backend inoltrano sempre il token accesso Supabase in `Authorization: Bearer <token>`. Android/iOS dovranno seguire lo stesso contratto bearer. Il backend valida i bearer token utente esclusivamente tramite le signing keys/JWKS di Supabase e usa `SUPABASE_SECRET_KEY` per tutte le operazioni server-side privilegiate su Auth, Database e Storage. Non esiste più un cookie sessione backend per auth applicativa o stream SSE.

---

## Extraction providers

`ExtractionService` seleziona il provider tramite `LLM_PROVIDER`. Gemini invia i documenti
alle API Google; Ollama rasterizza i PDF per pagina e invia le immagini al server locale.
Entrambi rispettano `ExtractionProvider`, inclusi progressi, checkpoint persistiti e ripresa
dal chunk fallito. `OllamaClient` contiene il trasporto HTTP e valida il JSON completo, mentre
`OllamaProvider` gestisce pagine e callback. Normalizzazione, salvataggio offerte e crop restano
nel workflow applicativo esistente.

## Project Structure

```
girospesa-backend/
├── main.py                   # Entry point FastAPI: app, CORS, tutti i router
├── requirements.txt
├── docker-compose.yml        # Stack locale legacy/non ufficiale (tenuto solo come riferimento)
├── .env.example
│
├── api/routers/              # Endpoint HTTP organizzati per dominio
│   ├── users.py              # Profilo utente (Comune + raggio), avatar, eliminazione account
│   ├── lists.py              # CRUD lista spesa, gestione items, freshness offerte
│   ├── offers.py             # Offerte attive, ricerca e filtri
│   ├── flyers.py             # Upload volantini, listing volantini pubblici
│   ├── supermarkets.py       # Directory supermercati (pubblica)
│   ├── lists.py              # Lista singola + condivisione via inviti email
│   ├── push.py               # Iscrizioni Web Push/native
│   ├── purchases.py          # Storico acquisti, tracking risparmio
│   ├── analytics.py          # Analytics B2B anonimizzata (API key auth)
│   ├── contact_requests.py   # Contatti pubblici, bug report, collaborazione, volantini mancanti
│
├── core/                     # Infrastruttura condivisa
│   ├── config.py             # Pydantic settings da .env
│   ├── auth.py               # Verifica JWT Supabase, check ruolo admin
│   └── database.py           # Singleton client Supabase
│
├── services/                 # Logica di business
│   ├── deal_freshness.py     # Classifica freshness offerte in lista (fresh/expired/price_changed)
│   ├── flyer_cleanup.py      # Eliminazione notturna volantini scaduti (APScheduler, midnight Europe/Rome)
│   ├── purchased_items_cleanup.py # Rimozione notturna item già acquistati da liste spesa
│   └── push_notify.py        # Invio Web Push con VAPID
│
├── utils/
│
└── tests/
    ├── unit/                 # Test unitari (nessuna infrastruttura)
    ├── integration/          # Test di integrazione (richiede `supabase start`)
    └── performance/          # Benchmark DB e upload
```

---

## Coordinate immagini

`BoundingBox` è un value object privo di dipendenze infrastrutturali: valida coordinate normalizzate e rende espliciti assi X/Y. La normalizzazione produce array X/Y versione 2. Il confine di persistenza interpreta anche le righe versione 1 senza modificarle. Ritaglio e lettore condividono la convenzione X/Y; il lettore riceve sempre coordinate canoniche dall’API.
