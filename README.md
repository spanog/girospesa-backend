# Lista Spesa Furba — Backend

FastAPI backend per Lista Spesa Furba. Contiene tutta la logica di business dell'applicazione: ottimizzazione lista della spesa, gestione liste condivise, geocoding indirizzi, push notification, analytics B2B e gestione del catalogo prodotti.

> **Estrazione AI volantini:** questo repo contiene runtime, review flow e CLI di valutazione. Il vecchio workspace di estrazione è stato assorbito in questo backend.

---

## Come si colloca nel sistema

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Lista Spesa Furba                            │
│                                                                      │
│  ┌────────────────────┐           ┌─────────────────────────────┐   │
│  │  lista-spesa-      │  REST API │  lista-spesa-               │   │
│  │  furba-webapp/     │ ────────▶ │  furba-backend/             │   │
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

Il frontend si autentica tramite Supabase Auth (client-side), ottiene un JWT e lo passa come `Authorization: Bearer <token>` a questo backend. Il backend verifica il JWT con il secret condiviso per token `HS256` legacy oppure tramite JWKS Supabase per token `ES256`, e usa la service role key per tutte le operazioni su Supabase.

---

## Struttura del progetto

```
lista-spesa-furba-backend/
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
│   ├── invite.py             # Inviti lista condivisa (token-based)
│   ├── push.py               # Iscrizioni Web Push + webhook notifiche preferiti
│   ├── purchases.py          # Storico acquisti, tracking risparmio
│   ├── analytics.py          # Analytics B2B anonimizzata (API key auth)
│   ├── flyer_requests.py     # Richieste utente per nuovi volantini + email admin
│   └── admin_products.py     # CRUD prodotti e offerte (admin only)
│
├── core/                     # Infrastruttura condivisa
│   ├── config.py             # Pydantic settings da .env
│   ├── auth.py               # Verifica JWT Supabase, check ruolo admin
│   └── database.py           # Singleton client Supabase
│
├── services/                 # Logica di business
│   ├── deal_freshness.py     # Classifica freshness offerte in lista (fresh/expired/price_changed)
│   ├── flyer_cleanup.py      # Eliminazione notiturna volantini scaduti (APScheduler, midnight Europe/Rome)
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

## Endpoint API

### Autenticazione

Il backend usa tre livelli di autenticazione:

| Tipo | Come funziona | Usato da |
|------|---------------|----------|
| **Utente autenticato** | JWT Supabase in header `Authorization: Bearer <token>` | Quasi tutti gli endpoint |
| **Admin** | JWT + `app_metadata.role == "admin"` | `/admin/*` |
| **API key B2B** | Header `X-API-Key: <key>` | `GET /analytics/b2b` |
| **Webhook secret** | Header `X-Webhook-Secret: <secret>` | `POST /push/notify-favorites` |

### Utenti (`/users`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/users/me` | ✅ | Profilo utente autenticato |
| `PUT` | `/users/me` | ✅ | Aggiorna profilo; auto-geocode se cambia indirizzo |
| `POST` | `/users/geocode` | ✅ | Geocodifica indirizzo di casa → aggiorna `home_lat/lng` e `home_location` PostGIS |
| `POST` | `/users/me/avatar` | ✅ | Upload avatar (JPEG/PNG/WebP, max 5 MB) → bucket `avatars` |
| `DELETE` | `/users/me` | ✅ | Elimina account + tutti i dati utente (GDPR) |

### Lista spesa (`/lists`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/lists/active` | ✅ | Lista spesa attiva; auto-crea se non esiste; arricchisce gli item con `category` e `subcategory` |
| `POST` | `/lists/{id}/items` | ✅ | Aggiunge item (manuale o da offerta) e salva snapshot `category`/`subcategory` quando collegato a prodotto/offerta |
| `DELETE` | `/lists/{id}/items/{item_id}` | ✅ | Rimuove item |
| `POST` | `/lists/{id}/items/{item_id}/toggle` | ✅ | Check/uncheck item; registra `checked_by`, `checked_at` |
| `POST` | `/lists/{id}/invite` | ✅ owner | Crea link invito (token 64 char, TTL 7 giorni) |
| `GET` | `/lists/{id}/members` | ✅ member | Lista membri lista condivisa |
| `DELETE` | `/lists/{id}/members/{user_id}` | ✅ owner | Rimuove membro |
| `GET` | `/lists/{id}/deal-freshness` | ✅ member | Freshness di tutte le offerte pinnate nella lista |

### Prodotti e offerte (`/products`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/products` | ❌ | Offerte attive con full-text search (`q`), filtri `category`, `supermarket`, paginazione |
| `GET` | `/products/{id}` | ❌ | Dettaglio singola offerta (prodotto + supermercato) |
| `GET` | `/products/{id}/similar` | ❌ | Altre offerte attive per lo stesso prodotto canonico (ordinate per prezzo) |

Nota implementativa: ordinamento `/products` usa query builder PostgREST Python con keyword `nullsfirst` per mantenere stabile ordinamento default e per scadenza.

### Preferiti (`/favorites`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/favorites` | ✅ | Lista preferiti con miglior offerta attiva per ciascuno |
| `GET` | `/favorites/{product_id}` | ✅ | Controlla se un prodotto è tra i preferiti |
| `POST` | `/favorites` | ✅ | Aggiunge ai preferiti (body: `{product_id}`) |
| `DELETE` | `/favorites/{product_id}` | ✅ | Rimuove dai preferiti |

### Volantini (`/flyers`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/flyers` | ✅ admin/manager | Lista volantini gestibili; i manager vedono solo il proprio supermercato |
| `GET` | `/flyers/public` | ❌ | Lista volantini pubblici completati con almeno un'offerta confermata |
| `GET` | `/flyers/{flyer_id}` | ✅ admin/manager | Dettaglio singolo volantino |
| `POST` | `/flyers/upload` | ✅ admin/manager | Upload volantino (PDF/JPG/PNG/WebP, max 50 MB); crea riga `status='pending'` |
| `POST` | `/flyers/{flyer_id}/extract` | ✅ admin/manager | Avvia estrazione AI per un volantino pending/error |
| `GET` | `/flyers/{flyer_id}/draft-offers` | ✅ admin/manager | Lista offerte estratte ma non confermate |
| `PATCH` | `/flyers/{flyer_id}/draft-offers/{offer_id}` | ✅ admin/manager | Modifica inline di una draft offer e dei campi prodotto collegati |
| `POST` | `/flyers/{flyer_id}/offers/confirm` | ✅ admin/manager | Conferma tutte le offerte draft e le rende pubbliche |
| `POST` | `/flyers/admin/cleanup` | 👑 admin | Trigger manuale pulizia volantini scaduti (eseguita automaticamente ogni mezzanotte) |

### Contratto prezzi estrazione

- Il backend accetta sia il prompt legacy (`price_offer`, `category`, `subcategory`) sia il prompt v2 (`price_current`, `category_main`, `category_sub`, `discount_percentage`, `price_per_unit`, `price_per_unit_measure`).
- `price_original` resta il prezzo pieno/non in offerta solo se stampato sul volantino. Non viene mai inferito.
- `offers` salva anche il prezzo unitario strutturato:
  - `unit_price_value NUMERIC(8,2)`
  - `unit_price_unit TEXT` con valori ammessi `kg`, `l`, `kg sgocc`
  - `unit_price TEXT` come label derivata per compatibilità
- Gli endpoint che restituiscono offerte (`/products`, `/flyers/{flyer_id}/draft-offers`, `/favorites`, `/optimize`) espongono anche `unit_price_value`, `unit_price_unit`, `unit_price_label`.

### Ottimizzazione (`/optimize`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/optimize` | ✅ | Ottimizza lista spesa → gruppi per supermercato con risparmio e alternative |

### Supermercati (`/supermarkets`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/supermarkets` | ❌ | Directory supermercati attivi, ordinati per nome; con `lat`, `lng`, `max_distance_km` restituisce solo quelli vicini con `distance_km` |

### Inviti (`/invite`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/invite/{token}` | ❌ | Valida token; restituisce nome lista e chi ha invitato |
| `POST` | `/invite/{token}/accept` | ✅ | Accetta invito e diventa membro della lista |

### Push notification (`/push`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/push/subscribe` | ✅ | Registra subscription Web Push del browser |
| `POST` | `/push/unsubscribe` | ✅ | Cancella subscription |
| `POST` | `/push/notify-favorites` | Webhook secret | Webhook Supabase: nuova offerta → notifica agli utenti che hanno quel prodotto tra i preferiti |

### Acquisti (`/purchases`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/purchases/items/{item_id}` | ✅ | Segna item come acquistato; registra prezzo e risparmio |
| `DELETE` | `/purchases/items/{item_id}` | ✅ | Annulla acquisto |
| `GET` | `/purchases/history` | ✅ | Storico risparmio (ultimi N giorni, default 90) |

### Analytics B2B (`/analytics`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/analytics/b2b` | API key | Top prodotti ricercati + totale liste (dati anonimi aggregati per catene GDO) |

### Admin (`/admin/products`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/admin/products` | 👑 admin | Catalogo prodotti con filtri (`q`, `category`, `subcategory`, `archived`, `no_image`) e paginazione |
| `POST` | `/admin/products` | 👑 admin | Crea prodotto manuale |
| `GET` | `/admin/products/{id}` | 👑 admin | Dettaglio prodotto con tutte le offerte |
| `PATCH` | `/admin/products/{id}` | 👑 admin | Modifica prodotto |
| `POST` | `/admin/products/{id}/archive` | 👑 admin | Archivia prodotto (soft delete) |
| `POST` | `/admin/products/{id}/restore` | 👑 admin | Ripristina prodotto archiviato |
| `POST` | `/admin/products/{id}/image` | 👑 admin | Upload immagine prodotto → bucket `product-images` |
| `PATCH` | `/admin/products/{id}/offers/{oid}` | 👑 admin | Modifica offerta |
| `DELETE` | `/admin/products/{id}/offers/{oid}` | 👑 admin | Elimina offerta |
---

## Scheduled Jobs

The backend runs scheduled background jobs via APScheduler (`AsyncIOScheduler`), started in the FastAPI lifespan context manager in `main.py`.

| Job | Schedule | Service | Description |
|-----|----------|---------|-------------|
| `flyer_cleanup` | Daily at 00:00 Europe/Rome | `services/flyer_cleanup.py` | Deletes flyers where `valid_to < today`. Removes the Supabase Storage file (best-effort) and the DB row. `offers.flyer_id` is set to NULL via ON DELETE SET NULL — offers are not deleted. Flyers with `valid_to = NULL` are never auto-deleted. |

To trigger cleanup manually (ops or testing):

```bash
curl -X POST http://localhost:8000/flyers/admin/cleanup \
  -H "Authorization: Bearer <admin-jwt>"
# {"deleted": N}
```

---

## Note schema e RLS

- `analytics_data`, `extraction_log` e `flyer_requests` sono tabelle interne. RLS resta abilitato senza policy `anon`/`authenticated`; accesso e scrittura passano dal backend con `SUPABASE_SERVICE_ROLE_KEY`.
- Le richieste volantino guest e autenticate passano sempre da `POST /flyer-requests`. Non esiste piu un path supportato con insert diretto client -> Supabase.
- Log estrazione canonico: `extraction_log`. Eventuali ambienti locali legacy con `scraping_log` vengono riallineati dalla migration di hardening.
- PostGIS è abilitato nello schema `extensions`. `supermarkets.location`, `user_profiles.home_location` e `user_profiles.search_location` sono `geography(Point, 4326)` indicizzate GiST; la RPC `nearby_supermarkets` usa `ST_DWithin` e `ST_Distance`.

## Flussi principali

### 1. Ottimizzazione lista spesa

```
Frontend
  POST /optimize {list_id, mode: "maximize_savings" | "minimize_stores"}
                    │
                    ▼
  Carica items non spuntati dalla lista
  Carica posizione utente (search_location, oppure home_location)
  Carica tutte le offerte attive con prodotto + supermercato
                    │
                    ▼ per ogni item
  Fuzzy-match item vs offerte (difflib, soglia 0.5)
  Filtra per distanza con PostGIS (`nearby_supermarkets`, `ST_DWithin`)
                    │
                    ▼
  Greedy set-cover loop:
    ┌─────────────────────────────────────────┐
    │  Assegna un punteggio a ogni negozio    │
    │  (coverage × risparmio, o viceversa)    │
    │  Scegli il negozio migliore             │
    │  Assegna tutti gli item che copre       │
    │  Ripeti finché nessun item rimane       │
    └─────────────────────────────────────────┘
                    │
                    ▼
  Risposta: store_groups[{supermercato, prodotti, subtotal,
            savings, distanza_km, alternative per item}]
```

### 2. Upload volantino e estrazione AI

```
  Utente carica PDF → POST /flyers/upload
                         │
                         ▼
  Backend: valida file (tipo + dimensione)
  Backend: calcola SHA-256 → controlla duplicati (409 se già esiste)
  Backend: carica su Supabase Storage (bucket flyers)
  Backend: crea riga flyers con status='pending'
                         ▼
  Admin / manager: POST /flyers/{id}/extract
                         │
                         ▼
  ExtractionService:
    → scarica file
    → Gemini estrae prodotti
    → normalizza prodotti
    → upsert products + insert offers con is_confirmed=false
    → aggiorna status → 'done'
                         │
                         ▼
  Admin / manager:
    GET /flyers/{id}/draft-offers
    PATCH draft offers
    POST /flyers/{id}/offers/confirm
                         │
                         ▼
  Frontend pubblico: GET /products / GET /flyers/public
```

### 3. Push notification su nuova offerta

```
  Browser utente: richiede permesso notifiche
  Browser: genera subscription {endpoint, p256dh, auth_key}
  Frontend: POST /push/subscribe
  Backend: salva in push_subscriptions (upsert per user_id + endpoint)
                         │
  Admin o manager conferma nuove offerte
  INSERT into offers ──▶ Supabase trigger
                         │
                         ▼
  Webhook: POST /push/notify-favorites  (X-Webhook-Secret)
    Backend: legge product_id + supermarket_name dalla nuova offerta
    Backend: cerca utenti che hanno quel prodotto tra i preferiti
    Per ogni utente:
      check notification_favorites preference
      → fetch push_subscriptions
      → per ogni subscription:
            send_push_notification (VAPID, pywebpush)
            se 410 Gone → cancella subscription stale
```

### 4. Lista condivisa

```
  Proprietario: POST /lists/{id}/invite
    Backend: genera token (64 char hex), expires_at = +7 giorni
    Backend: inserisce list_invites row
                    │
  Condivide link: /invite/{token}
                    │
  Ospite: GET /invite/{token}   → valida token, mostra nome lista
  Ospite: accede, fa login su Supabase
  Ospite: POST /invite/{token}/accept
    Backend: inserisce list_members {role: 'member'}
    Backend: segna invite → accepted
                    │
  Ora entrambi vedono la lista in tempo reale
  (Supabase Realtime su shopping_lists.items, list_members)
```

### 5. Freshness delle offerte in lista

```
  Utente aggiunge item con pinned_offer_id (da ottimizzazione o ricerca)
  Volantino scade o prezzo cambia
                    │
  Frontend: GET /lists/{id}/deal-freshness
    Backend: per ogni item con pinned_offer_id:
      ┌──────────────────────────────────────────────┐
      │  offer non trovata       → unavailable        │
      │  is_active = false       → expired            │
      │  prezzo cambiato > 0.01€ → price_changed      │
      │  tutto ok                → fresh              │
      └──────────────────────────────────────────────┘
    Ritorna stato + prezzo attuale per ogni item
  Frontend: mostra badge/warning su prodotti scaduti o variati
```

---

## Avvio locale

Workflow ufficiale:

- Docker solo per servizi Supabase locali
- FastAPI come processo host separato
- Next.js come processo host separato nel repo `lista-spesa-furba-webapp/`
- Volumi Docker nominati su PostgreSQL e Storage: restart normali preservano dati locali

### 1. Prerequisiti

- Docker + Docker Compose
- Python 3.11+
- Node.js 20+ (per il frontend separato)

### 2. Variabili d'ambiente

```bash
cp .env.example .env
cp .env.test.example .env.test
```

Valori locali canonici:

- `SUPABASE_URL=http://127.0.0.1:54321`
- `FRONTEND_URL=http://127.0.0.1:3000`
- `GEOCODING_PROVIDER=disabled` per evitare chiamate esterne in locale
- `GOOGLE_API_KEY` richiesto solo se si vuole usare estrazione AI Gemini
- `SUPABASE_SERVICE_ROLE_KEY` e `SUPABASE_JWT_SECRET` si copiano da `supabase status -o env`
- `ADMIN_EMAIL` e `ADMIN_PASSWORD` servono per seedare utente admin via API service-role
- `.env` backend deve contenere solo variabili lette da FastAPI; credenziali Docker/Supabase CLI come `POSTGRES_PASSWORD`, `ANON_KEY`, `JWT_SECRET` e `SERVICE_ROLE_KEY` non vanno copiate qui
- `.env.test` e' locale-only ed e' ignorato da Git; il template tracciato resta `.env.test.example`

### 3. Avviare lo stack Supabase locale

```bash
supabase start
```

Per copiare le variabili locali aggiornate:

```bash
supabase status -o env
```

Servizi disponibili dopo l'avvio:

| Servizio | URL | Scopo |
|----------|-----|-------|
| Supabase Studio | `http://localhost:54323` | Admin UI (DB, Auth, Storage) |
| Supabase API | `http://127.0.0.1:54321` | `SUPABASE_URL` locale per backend e frontend |
| PostgreSQL | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` | DB diretto |
| Mailpit | `http://127.0.0.1:54324` | Inbox email locale |

Bootstrap admin condiviso per locale/test/prod:

- Schema canonico: `../lista-spesa-furba-webapp/supabase/migrations/*.sql`
- SQL seed locale: `supabase/seed.sql` non crea piu' admin auth
- Script Python canonico: `.venv/bin/python -m scripts.seed_admin`
- Alias task locale: `.venv/bin/task dev-setup`
- Input richiesti:
  - `ADMIN_EMAIL`
  - `ADMIN_PASSWORD`
- Output garantiti:
  - utente auth esiste
  - `app_metadata.role = "admin"`
  - `public.user_profiles.role = 'admin'`
- Script e' idempotente:
  - crea admin se manca
  - se esiste gia', non duplica utente
  - riallinea ruolo JWT/profile se necessario
- Credenziali locali gia' configurate in `.env`:
  - `ADMIN_EMAIL=dev-admin@local.test`
  - `ADMIN_PASSWORD=Admin123!`
- L'admin non viene creato automaticamente all'avvio app: dopo primo `supabase start`
  o dopo `supabase db reset`, esegui `.venv/bin/python -m scripts.seed_admin`.

Login consigliato per primo accesso:

- Frontend: `http://localhost:3000/login`
- Supabase Studio/Auth inspect: `http://localhost:54323`

Comandi utili:

```bash
supabase status
supabase status -o env
.venv/bin/task dev-setup                      # alias locale per seed admin
.venv/bin/python -m scripts.seed_admin         # seed idempotente admin
.venv/bin/python -m scripts.seed_admin --check # verifica auth user, ruolo DB, login password grant
supabase stop                  # preserva dati locali
supabase db reset              # reset totale locale: utenti, prodotti, offerte, storage
```

> `supabase stop` preserva volumi e dati. Dopo `supabase db reset` o su nuovo ambiente, riesegui `.venv/bin/python -m scripts.seed_admin`.
>
> `supabase status -o env` espone chiavi locali correnti: `ANON_KEY`, `PUBLISHABLE_KEY`, `SERVICE_ROLE_KEY`, `JWT_SECRET`.
>
> `supabase db reset` resta comando di riallineamento totale quando vuoi ripartire da zero.

### 4. Avviare FastAPI

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
.venv/bin/task dev-setup
python -m uvicorn main:app --reload --port 8000
```

- API locale: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Se usi Python < 3.11, boot fallisce subito con errore esplicito prima di caricare router/app.
- Se shell mostra `zsh: command not found: uvicorn`, virtualenv non e' attivo oppure dipendenze non sono ancora installate.
- `requirements.txt` tiene `httpx==0.27.2` per compatibilita' con `supabase==2.10.0` (`supabase` richiede `httpx<0.28`).
- Avvio equivalente senza activation:

```bash
.venv/bin/python -m uvicorn main:app --reload --port 8000
```

### 4.1 Primo avvio locale completo

```bash
# Terminale 1
cd lista-spesa-furba-backend
supabase start

# Terminale 2
cd lista-spesa-furba-backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.seed_admin
python -m uvicorn main:app --reload --port 8000

# Terminale 3
cd ../lista-spesa-furba-webapp
npm install
npm run dev
```

Credenziali admin locale configurate:

- email: `dev-admin@local.test`
- password: `Admin123!`

Queste credenziali diventano valide solo dopo il seed manuale:

```bash
cd lista-spesa-furba-backend
.venv/bin/task dev-setup
```

Check rapido seed:

```bash
cd lista-spesa-furba-backend
.venv/bin/python -m scripts.seed_admin --check
```

Comando rapido alternativo:

```bash
npx taskipy dev
```

### 5. Avviare il frontend separato

Dal repo fratello `../lista-spesa-furba-webapp/`:

```bash
npm install
npm run dev
```

- Frontend locale: `http://localhost:3000`
- Tutte le chiamate business passano a FastAPI; Next.js non espone API routes

### 6. CLI e valutazione estrazione

I CLI di valutazione e QA vivono in `scripts/extraction/`. Il runtime ufficiale resta `ExtractionService`, invocato dagli endpoint `POST /flyers/{flyer_id}/extract`.

### Local Dependency Matrix

| Tipo | Elemento | Richiesto per boot locale | Note |
|------|----------|---------------------------|------|
| Supabase CLI | Postgres/Auth/Storage/Studio | Sì | Avvio via `supabase start` |
| Processo host | FastAPI backend | Sì | Porta `8000` |
| Processo host | Next.js frontend | Sì | Repo separato, porta `3000` |
| API esterna | Google Gemini | Solo per estrazione volantini | Unica dipendenza esterna richiesta per AI extraction |
| Servizio esterno | Nominatim | No | Geocoding disabilitato di default in locale |
| Servizio esterno | Resend | No | Email admin opzionali |

---

## Testing

### Test unitari (nessuna infrastruttura)

```bash
pytest tests/unit -v
pytest tests/ -v --ignore=tests/integration   # tutto tranne integration
```

### Test di integrazione (richiede `supabase start`)

```bash
# 1. Avvia lo stack locale
supabase start

# 2. Prepara il file locale dei test
cp .env.test.example .env.test

# 3. Esegui i test
pytest tests/integration -v
```

FastAPI non deve essere avviato separatamente: i test HTTP usano l'app in-process via HTTPX/ASGI.

I test di integrazione coprono: geocoding, ottimizzazione, upload volantino, lifecycle preferiti, inviti lista, eliminazione account (GDPR).

### Test di performance (richiede `supabase start`)

```bash
supabase start
cp .env.test.example .env.test
pytest tests/performance -v -s
```

## Git hygiene per primo push

- `main` resta riservato a codice pronto per deploy.
- Sviluppo V1: usare un branch dedicato, per esempio `codex/v1-dev`.
- File locali come `.env`, `.env.test`, cache Python, artefatti coverage e stato locale Supabase non vanno mai pushati.

| File | Cosa misura | Soglia |
|------|-------------|--------|
| `test_db_performance.py` | FTS + filtro offerte attive su 10.000 prodotti | < 500 ms |
| `test_optimizer_performance.py` | `POST /optimize` con lista 50 item + 1.000 offerte | < 2 s |
| `test_upload_performance.py` | Upload PDF da 10 MB (40 pagine equiv.) | < 5 s |

### Core Web Vitals (frontend — Playwright)

```bash
# Dal repo frontend (lista-spesa-furba-webapp/)
npm run test:e2e -- performance-cwv
```

Soglie: LCP < 2500ms, CLS < 0.1, INP < 200ms sulla pagina `/offerte`.

---

## Variabili d'ambiente

```bash
# ── Local dev obbligatorio per backend boot ----------------------------------
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_SERVICE_ROLE_KEY=<local-service-role-key>
SUPABASE_JWT_SECRET=<local-jwt-secret>
FRONTEND_URL=http://127.0.0.1:3000

# ── Gemini extraction (solo se usi estrazione AI) ---------------------------
LLM_PROVIDER=gemini
GOOGLE_API_KEY=<google-api-key>
GEMINI_MODEL=gemma-4-31b-it

# ── Servizi esterni opzionali in locale -------------------------------------
GEOCODING_PROVIDER=disabled          # usa "nominatim" solo se vuoi geocoding reale
RESEND_API_KEY=
ADMIN_NOTIFICATION_EMAIL=

# ── Web Push / webhook opzionali --------------------------------------------
VAPID_PRIVATE_KEY=
VAPID_PUBLIC_KEY=
VAPID_MAILTO=mailto:admin@listaspesafurba.it
WEBHOOK_SECRET=

# ── Copia da `supabase status -o env` ---------------------------------------
# SUPABASE_SERVICE_ROLE_KEY <- SERVICE_ROLE_KEY
# SUPABASE_JWT_SECRET       <- JWT_SECRET

# ── Admin seed condiviso -----------------------------------------------------
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change-me
```

Seed admin da eseguire dopo setup locale o in deploy:

- `.venv/bin/python -m scripts.seed_admin`
- `.venv/bin/python -m scripts.seed_admin --check`

Flow identica in locale, test, prod: cambia solo valore env.

---

## Servizi esterni

| Servizio | Scopo | Configurazione | Note |
|----------|-------|----------------|------|
| **Google Gemini** | Estrazione AI volantini | `GOOGLE_API_KEY` + `GEMINI_MODEL` | Unica dipendenza esterna richiesta quando usi AI extraction |
| **Nominatim (OpenStreetMap)** | Geocoding indirizzi | `GEOCODING_PROVIDER=nominatim` | Opzionale, disabilitato di default in locale |
| **Resend** | Email transazionali (richieste volantini) | `RESEND_API_KEY` | Opzionale, fallisce gracefully |
| **Web Push (VAPID)** | Notifiche browser | Coppia VAPID + `WEBHOOK_SECRET` | Standard W3C, nessun servizio proprietario |

---

## Bucket Supabase Storage

| Bucket | Pattern path | Scopo | Max dimensione | Accesso |
|--------|-------------|-------|----------------|---------|
| `avatars` | `{user_id}.{jpg\|png\|webp}` | Foto profilo utente | 5 MB | URL pubblico |
| `flyers` | `{user_id}/{uuid}.{pdf\|jpg}` | Volantini caricati (pre-estrazione) | 50 MB | URL pubblico |
| `product-images` | `{product_id}/{uuid}.{ext}` | Immagini prodotti (admin) | — | URL pubblico |

---

## Schema Analytics B2B

La tabella `analytics_data` contiene metriche aggregate e anonimizzate per le catene GDO:

| Campo | Tipo | Descrizione |
|-------|------|-------------|
| `id` | UUID | PK |
| `week_start` | date | Settimana di riferimento |
| `metric_type` | string | `offer_efficacia`, `categoria_trend`, `conversion_rate`, `sconto_benchmark` |
| `category` | string | Categoria prodotto (opzionale) |
| `supermarket_id` | string | Supermercato (opzionale) |
| `value` | float | Valore della metrica |
| `description` | text | Note opzionali |
| `created_at` | timestamptz | Timestamp inserimento |

Migrazione: `supabase/migrations/20260415075251_analytics_schema.sql`. I dati sono sempre anonimi e GDPR-compliant — nessuna informazione personale.
RLS resta abilitato anche su questa tabella; accesso previsto solo tramite backend/service role.
