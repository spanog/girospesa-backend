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
│  │  Frontend SPA      │ ◀──────── │  /products  /optimize       │   │
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

## Project Structure

```
girospesa-backend/
├── main.py                   # Entry point FastAPI: app, CORS, tutti i router
├── requirements.txt
├── docker-compose.yml        # Stack locale legacy/non ufficiale (tenuto solo come riferimento)
├── .env.example
│
├── api/routers/              # Endpoint HTTP organizzati per dominio
│   ├── users.py              # Profilo utente, geocoding, avatar, eliminazione account
│   ├── lists.py              # CRUD lista spesa, gestione items, freshness offerte
│   ├── products.py           # Catalogo offerte attive, ricerca full-text, prodotti simili
│   ├── favorites.py          # Prodotti preferiti per utente
│   ├── flyers.py             # Upload volantini, listing volantini pubblici
│   ├── optimize.py           # Algoritmo greedy set-cover per ottimizzazione lista
│   ├── supermarkets.py       # Directory supermercati (pubblica)
│   ├── lists.py              # Lista singola + condivisione via inviti email
│   ├── push.py               # Iscrizioni Web Push + webhook notifiche preferiti
│   ├── purchases.py          # Storico acquisti, tracking risparmio
│   ├── analytics.py          # Analytics B2B anonimizzata (API key auth)
│   ├── contact_requests.py   # Contatti pubblici, bug report, collaborazione, volantini mancanti
│   └── admin_products.py     # CRUD prodotti e offerte (admin only)
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
│   ├── geocoding.py          # Geocoding indirizzi via Nominatim (OpenStreetMap)
│   └── push_notify.py        # Invio Web Push con VAPID
│
├── utils/
│
└── tests/
    ├── unit/                 # Test unitari (nessuna infrastruttura)
    ├── integration/          # Test di integrazione (richiede `supabase start`)
    └── performance/          # Benchmark DB, optimizer, upload
```

---
