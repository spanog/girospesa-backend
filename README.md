# GiroSpesa — Backend

FastAPI backend per GiroSpesa. Contiene tutta la logica di business dell'applicazione: ottimizzazione lista della spesa, gestione liste condivise, geocoding indirizzi, push notification, analytics B2B e gestione del catalogo prodotti.

> **Estrazione AI volantini:** questo repo contiene runtime, review flow e CLI di valutazione. Il vecchio workspace di estrazione è stato assorbito in questo backend.

---

## Come si colloca nel sistema

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

Il frontend si autentica tramite Supabase Auth (client-side), ottiene un JWT e lo passa come `Authorization: Bearer <token>` a questo backend. Il backend verifica il JWT con il secret condiviso per token `HS256` legacy oppure tramite JWKS Supabase per token `ES256`, e usa la service role key per tutte le operazioni su Supabase.

---

## Struttura del progetto

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

Le liste non-default possono essere eliminate solo dal proprietario. Se lista condivisa viene rimossa, backend riallinea gli `active_list_id` dei membri alla loro `Lista principale`, crea una `app_notification` persistente per ogni membro attivo e prova anche l'invio Web Push se esiste una subscription. Anche la rimozione di un singolo membro da una lista condivisa riallinea l'`active_list_id` del target alla sua `Lista principale` e genera notifica persistente + Web Push solo per l'utente rimosso. Lo stesso endpoint supporta anche il self-leave: un `member` può uscire dalla lista condivisa rimuovendo solo la propria membership; in quel caso il fallback della lista attiva avviene sul membro uscente e la notifica inbox/Web Push viene inviata solo al proprietario della lista. Condivisione e gestione inviti restano owner-only: solo il proprietario può creare inviti email o token, elencare inviti pendenti e revocarli; un membro condiviso può solo leggere i membri della lista e lasciare la propria membership. Ogni read/write lista resta limitato a proprietario o membri condivisi; un invito pending non concede accesso finché non viene accettato. Nei deploy con accesso Postgres diretto, la creazione lista owned gestisce anche il breve lag tra `auth.admin.create_user` e visibilità della riga in `auth.users`, ritentando l'insert dopo attesa breve invece di fallire con FK race.

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/lists/active` | ✅ | Lista attiva; auto-crea `Lista principale` se non esiste; arricchisce gli item con `category` e `subcategory` |
| `POST` | `/lists/{id}/reset` | ✅ member | Svuota la lista corrente dopo conferma frontend e restituisce la lista aggiornata |
| `POST` | `/lists/{id}/items` | ✅ member | Aggiunge item (manuale o da offerta) e salva snapshot `category`/`subcategory` quando collegato a prodotto/offerta; persistenza via RPC concorrente-safe `append_list_item` (`SECURITY INVOKER`, `search_path` fissato a `public`) |
| `PATCH` | `/lists/{id}/items/{item_id}` | ✅ member | Aggiorna quantità o alternativa selezionata; con `pinned_offer_id` salva `source`, `pinned_product_id`, `found_deals`, categoria e sottocategoria coerenti via RPC concorrente-safe `update_list_item` (`SECURITY INVOKER`, protetta da RLS + `auth.uid()`), poi rilegge item persistito |
| `DELETE` | `/lists/{id}/items/{item_id}` | ✅ member | Rimuove item via RPC concorrente-safe `remove_list_item` (`SECURITY INVOKER`, `search_path` fissato a `public`) |
| `POST` | `/lists/{id}/items/{item_id}/toggle` | ✅ member | Check/uncheck item; registra `checked_by`, `checked_at` |
| `POST` | `/lists/{id}/invite` | ✅ owner | Crea link invito (token 64 char, TTL 7 giorni) |
| `GET` | `/lists/{id}/members` | ✅ member | Lista membri lista condivisa |
| `DELETE` | `/lists/{id}/members/{user_id}` | ✅ owner/member(self) | Owner rimuove un altro membro oppure un member lascia la lista da solo; riallinea `active_list_id` del target alla `Lista principale` e notifica solo parte interessata (utente rimosso oppure proprietario) |
| `GET` | `/lists/{id}/deal-freshness` | ✅ member | Freshness di tutte le offerte pinnate nella lista |

### Prodotti e offerte (`/products`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/products` | ❌ | Offerte attive con ricerca `q` ibrida (`word_similarity` + match per prefisso/sottostringa), filtri `category`, `supermarket`, ordinamento default per nome prodotto, `sort=expiry` per scadenza crescente, `expiring_soon=true` per offerte che scadono entro 3 giorni, paginazione |
| `GET` | `/products/{id}` | ❌ | Dettaglio singola offerta (prodotto + supermercato) |
| `GET` | `/products/{id}/similar` | ❌ | Altre offerte attive per lo stesso prodotto canonico (ordinate per prezzo) |

Nota implementativa: ordinamento `/products` usa query builder PostgREST Python. Il default ordina per `products.name`; `sort=expiry` ordina per `offers.valid_to` crescente con `NULL` dopo le offerte datate, poi per `products.name`. Il filtro `expiring_soon=true` usa stessa finestra temporale del contatore `expiring_soon_count`: `valid_to` compreso tra oggi e oggi + 3 giorni. La ricerca `q` passa da `public.search_products_catalog`, che mantiene ranking fuzzy con `word_similarity` ma include anche match per prefisso e sottostringa su nome/brand, così query come `mozza` trovano `Mozzarella` senza perdere tolleranza ai refusi.

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
| `POST` | `/flyers/{flyer_id}/extract` | ✅ admin/manager | Avvia estrazione AI per un volantino `pending` oppure riprende da chunk fallito se `status='error'` e `resume_available=true` |
| `GET` | `/flyers/{flyer_id}/draft-offers` | ✅ admin/manager | Lista offerte estratte ma non confermate |
| `PATCH` | `/flyers/{flyer_id}/draft-offers/{offer_id}` | ✅ admin/manager | Modifica inline di una draft offer e dei campi prodotto collegati |
| `POST` | `/flyers/{flyer_id}/offers/confirm` | ✅ admin/manager | Conferma tutte le offerte draft e le rende pubbliche |
| `POST` | `/flyers/admin/cleanup` | 👑 admin | Trigger manuale pulizia volantini scaduti (eseguita automaticamente ogni mezzanotte) |

### Contratto prezzi estrazione

- Il backend accetta sia il prompt legacy (`price_offer`, `category`, `subcategory`) sia il prompt v2 (`price_current`, `category_main`, `category_sub`, `discount_percentage`, `price_per_unit`, `price_per_unit_measure`).
- `price_original` resta il prezzo pieno/non in offerta solo se stampato sul volantino. Non viene mai inferito.
- `offers` salva anche il prezzo unitario strutturato:
  - `unit_price_value NUMERIC(8,2)`
  - `unit_price_unit TEXT` con valori ammessi `kg`, `L`, `kg sgocc`
  - `unit_price TEXT` come label derivata per compatibilità
- Gli endpoint che restituiscono offerte (`/products`, `/flyers/{flyer_id}/draft-offers`, `/favorites`, `/optimize`) espongono anche `unit_price_value`, `unit_price_unit`, `unit_price_label`.

### Contratto formato prodotto

- `products.format` non e più una stringa: è `JSONB` strutturato.
- Ogni prodotto canonico salva:
  - `format`: oggetto canonico compatto, senza campi `null` o default inutili
  - `format_key`: chiave canonica deterministica derivata da `format`
  - `format_label`: label leggibile derivata da `format`
- Identità prodotto canonico: `name + brand + format_key`.
- `format_label` è solo display/search aid. Non definisce unicità.
- Le API pubbliche e admin restituiscono sempre sia `format` sia `format_label`.
- Le API admin e draft-offer accettano solo `format` strutturato. Il backend rifiuta `format` testuale legacy.
- Il provider LLM deve emettere un `format` strutturato sparso: solo `tipo` e campi pertinenti, senza `null` superflui. Il backend resta source of truth per canonicalizzazione e compattazione.
- `format.varianti` è consentito solo in input estrazione LLM: il backend lo espande in prodotti/offerte distinti prima dell'upsert. Nessun prodotto persistito rappresenta un parent con varianti miste.
- Matching fuzzy/optimizer usa `name`, `brand`, `format_label`; mai JSON raw.
- Durante l'estrazione il backend deduplica prima in memoria su `(name, brand, format_key)`, fa batch upsert dei prodotti unici del volantino e registra timing per `provider`, `varianti`, `normalizzazione`, `dedupe`, `upsert prodotti`, `insert offerte`.
- Per PDF multipagina il backend divide il file in chunk PDF rigidi da 3 pagine e invia un chunk per volta a Gemini. Dopo ogni chunk riuscito persiste subito le draft offers di quel chunk e aggiorna `flyers.extraction_metadata` con pagina corrente, percentuale, `last_completed_chunk` e `next_chunk_*`, così il frontend può mostrare avanzamento live durante il polling e review parziale.
- Se un chunk fallisce dopo i retry, il flyer passa a `status='error'`, ma le draft offers dei chunk già riusciti restano salvate. `flyers.extraction_metadata` espone `resume_available`, `failed_chunk_*`, `next_chunk_*` e `partial_products_count`; una nuova `POST /flyers/{flyer_id}/extract` riparte dal primo chunk non completato correttamente senza duplicare le offerte già persistite. La ripresa si basa su `extraction_metadata` persistito, non sullo `status` transitorio del flyer mentre il retry è già tornato a `processing`.
- Quando Gemini fallisce o va in retry, backend logga anche contesto strutturato se disponibile: tipo eccezione, `code`, `status`, `message`, HTTP status/body e request id. Stesso dettaglio finisce in `retry_errors` dentro `extraction_log`.

### Ottimizzazione (`/optimize`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/optimize` | ✅ member | Ottimizza lista spesa → gruppi per supermercato con risparmio e alternative; accesso consentito solo ai membri della lista indicata |

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
| `POST` | `/push/notify-favorites` | Webhook secret | Webhook Supabase: nuova offerta pubblica, confermata e attiva → notifica agli utenti che hanno quel prodotto tra i preferiti |

Le notifiche Web Push di completamento/fallimento estrazione includono nel campo `data` anche `kind`, `flyer_id`, `status`, `products_count` e `url`. Il frontend usa questi campi per aggiornare subito la cache della gestione volantini e poi confermare lo stato tramite refetch HTTP.

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
| `DELETE` | `/admin/products/{id}` | 👑 admin | Elimina definitivamente prodotto senza offerte collegate; rimuove anche i preferiti collegati |
| `POST` | `/admin/products/{id}/image` | 👑 admin | Upload immagine prodotto → bucket `product-images` |
| `PATCH` | `/admin/products/{id}/offers/{oid}` | 👑 admin | Modifica offerta |
| `DELETE` | `/admin/products/{id}/offers/{oid}` | 👑 admin | Elimina offerta |
---

## Scheduled Jobs

The backend runs scheduled background jobs via APScheduler (`AsyncIOScheduler`), started in the FastAPI lifespan context manager in `main.py`.

- `flyer_cleanup` runs daily at 00:00 Europe/Rome and deletes expired flyers.
- `purchased_items_cleanup` runs daily at 00:00 Europe/Rome and removes purchased list items from previous Rome days, resetting the "Acquistati oggi" section automatically without touching purchase history.

### Note storico acquisti

- `purchase_history.product_id` resta valorizzabile come snapshot storico del prodotto acquistato, ma non mantiene più una foreign key verso `products`.
- `purchase_history.quantity` salva quantità acquistata; `price_paid`, `price_original` e `savings` nello storico sono importi totali già scalati per quantità.
- Questo permette di eliminare prodotti canonici non più usati senza perdere coerenza nello storico acquisti.

| Job | Schedule | Service | Description |
|-----|----------|---------|-------------|
| `flyer_cleanup` | Daily at 00:00 Europe/Rome | `services/flyer_cleanup.py` | Deletes flyers where `valid_to < today`. Removes the Supabase Storage file (best-effort) and the DB row. `offers.flyer_id` is set to NULL via ON DELETE SET NULL — offers are not deleted. Flyers with `valid_to = NULL` are never auto-deleted. |
| `purchased_items_cleanup` | Daily at 00:00 Europe/Rome | `services/purchased_items_cleanup.py` | Removes from each shopping list all items already purchased on previous Rome days. Items still purchased today stay visible in "Acquistati oggi" until midnight. Purchase history is not deleted. |

To trigger cleanup manually (ops or testing):

```bash
curl -X POST http://localhost:8000/flyers/admin/cleanup \
  -H "Authorization: Bearer <admin-jwt>"
# {"deleted": N}
```

---

## Note schema e RLS

- `analytics_data`, `extraction_log` e `flyer_requests` sono tabelle interne. RLS resta abilitato con policy esplicite `deny all`; accesso e scrittura passano solo dal backend con `SUPABASE_SERVICE_ROLE_KEY`.
- Le richieste volantino guest e autenticate passano sempre da `POST /flyer-requests`. Non esiste piu un path supportato con insert diretto client -> Supabase.
- Log estrazione canonico: `extraction_log`. Eventuali ambienti locali legacy con `scraping_log` vengono riallineati dalla migration di hardening.
- PostGIS è abilitato nello schema `extensions`. `supermarkets.location`, `user_profiles.home_location` e `user_profiles.search_location` sono `geography(Point, 4326)` indicizzate GiST; la RPC `nearby_supermarkets` usa `ST_DWithin` e `ST_Distance`.
- Il trigger DB di signup `public.handle_new_user()` crea `user_profiles` copiando `display_name` e campi indirizzo (`home_address`, `home_city`, `home_province`, `home_postal_code`) da `raw_user_meta_data`, poi crea la lista predefinita `Lista principale` e allinea `active_list_id`.

## Flussi principali

### 1. Ottimizzazione lista spesa

```
Frontend
  POST /optimize {list_id}
                    │
                    ▼
  Carica items non spuntati dalla lista
  Carica posizione utente (search_location, oppure home_location)
  Carica tutte le offerte attive nella finestra corrente (`valid_from <= oggi <= valid_to`, null-safe) con prodotto + supermercato
                    │
                    ▼ per ogni item
  Usa `pinned_offer_id` come default se ancora valido/vicino
  Usa `pinned_product_id` come match canonico se non c'è offerta specifica
  Gli item manuali restano nel gruppo `Senza offerta`
  Per gli item manuali usa ricerca fuzzy `pg_trgm` solo per le alternative
  Filtra per distanza con PostGIS (`nearby_supermarkets`, `ST_DWithin`)
                    │
                    ▼
  Raggruppa item con offerta per supermercato
  Raggruppa item manuali senza offerta separatamente
  Include alternative ordinate per prezzo crescente
  Se l'utente sceglie un'alternativa:
    PATCH /lists/{list_id}/items/{item_id}
    aggiorna pinned_offer_id, pinned_product_id, found_deals e categorie
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
- Next.js come processo host separato nel repo `girospesa-webapp/`
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
- `GEOCODING_PROVIDER=nominatim` in locale, così signup/profilo/seed admin riflettono comportamento reale durante sviluppo manuale
- `GOOGLE_API_KEY` richiesto solo se si vuole usare estrazione AI Gemini
- `SUPABASE_SERVICE_ROLE_KEY` e `SUPABASE_JWT_SECRET` si copiano da `supabase status -o env`
- `ADMIN_EMAIL` e `ADMIN_PASSWORD` servono per seedare utente admin via API service-role
- `.env` backend deve contenere solo variabili lette da FastAPI; credenziali Docker/Supabase CLI come `POSTGRES_PASSWORD`, `ANON_KEY`, `JWT_SECRET` e `SERVICE_ROLE_KEY` non vanno copiate qui
- `.env.test` e' locale-only ed e' ignorato da Git; i test integration iniettano questi valori solo dentro processo `pytest`, puntando allo stack Docker isolato su porte `55421`/`55422`, poi ripristinano l'env della sessione a fine run

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

- Schema canonico: `../girospesa-webapp/supabase/migrations/*.sql`
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
  - profilo admin con indirizzo `Via Palmiro Togliatti, 89024 Polistena (RC)`
  - almeno una `shopping_lists` attiva e vuota, con membership `owner`
- Script e' idempotente:
  - crea admin se manca
  - se esiste gia', non duplica utente
  - non crea liste duplicate se l'utente ha gia' una lista attiva
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
cd girospesa-backend
supabase start

# Terminale 2
cd girospesa-backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.seed_admin
python -m uvicorn main:app --reload --port 8000

# Terminale 3
cd ../girospesa-webapp
npm install
npm run dev
```

Credenziali admin locale configurate:

- email: `dev-admin@local.test`
- password: `Admin123!`

Queste credenziali diventano valide solo dopo il seed manuale:

```bash
cd girospesa-backend
.venv/bin/task dev-setup
```

Check rapido seed:

```bash
cd girospesa-backend
.venv/bin/python -m scripts.seed_admin --check
```

Comando rapido alternativo:

```bash
npx taskipy dev
```

### 5. Avviare il frontend separato

Dal repo fratello `../girospesa-webapp/`:

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
| Servizio esterno | Nominatim | No | Geocoding attivo di default in locale per prove manuali end-to-end |
| Servizio esterno | Resend | No | Email admin opzionali |

---

## Testing

### Test unitari (nessuna infrastruttura)

```bash
pytest tests/unit -v
pytest tests/ -v --ignore=tests/integration   # tutto tranne integration
```

### Test di integrazione (stack Docker isolato)

```bash
# 1. Prepara il file locale dei test
cp .env.test.example .env.test

# 2. Esegui i test: pytest avvia e distrugge solo i container integration
.venv/bin/python -m pytest tests/integration -v
```

Lo stack integration usa `docker-compose.integration.yml` con progetto Docker `girospesa-itest`, volumi dedicati e porte `55421` (API/Kong) + `55422` (PostgreSQL). Non usa `supabase start`, non legge `supabase status`, non cancella dati dello stack locale e non deve lasciare variabili integration esportate fuori dalla sessione `pytest`.

Comandi manuali utili:

```bash
.venv/bin/python -m scripts.integration_stack up
.venv/bin/python -m scripts.integration_stack status
.venv/bin/python -m scripts.integration_stack env
.venv/bin/python -m scripts.integration_stack down
```

FastAPI non deve essere avviato separatamente: i test HTTP usano l'app in-process via HTTPX/ASGI.

I test di integrazione coprono: geocoding, ottimizzazione, upload volantino, lifecycle preferiti, inviti lista, eliminazione account (GDPR).

### Test di performance (opt-in)

```bash
cp .env.test.example .env.test
RUN_PERFORMANCE_TESTS=1 .venv/bin/python -m pytest tests/performance -v -s
```

Performance benchmarks use the same isolated integration stack as integration tests. Normal `pytest tests` runs skip them to avoid machine-dependent timing failures.

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
# Dal repo frontend (girospesa-webapp/)
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
GEOCODING_PROVIDER=nominatim         # default locale: allinea sviluppo manuale a produzione
RESEND_API_KEY=
ADMIN_NOTIFICATION_EMAIL=

# ── Web Push / webhook opzionali --------------------------------------------
VAPID_PRIVATE_KEY=
VAPID_PUBLIC_KEY=
VAPID_MAILTO=mailto:admin@girospesa.it
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
| **Nominatim (OpenStreetMap)** | Geocoding indirizzi | `GEOCODING_PROVIDER=nominatim` | Default in locale per test manuali end-to-end; disabilitalo solo se vuoi evitare chiamate esterne |
| **Resend** | Email transazionali (richieste volantini) | `RESEND_API_KEY` | Opzionale, fallisce gracefully |
| **Web Push (VAPID)** | Notifiche browser | Coppia VAPID + `WEBHOOK_SECRET` | Standard W3C, nessun servizio proprietario |

---

## Bucket Supabase Storage

| Bucket | Pattern path | Scopo | Max dimensione | Accesso |
|--------|-------------|-------|----------------|---------|
| `avatars` | `{user_id}.{jpg\|png\|webp}` | Foto profilo utente | 5 MB | URL pubblico |
| `flyers` | `{user_id}/{uuid}.{pdf\|jpg}` | Volantini caricati (pre-estrazione) | 50 MB | URL pubblico |
| `product-images` | `{product_id}/{uuid}.{ext}` | Immagini prodotti (admin) | — | URL pubblico |

I bucket pubblici non espongono listing anonimo via `storage.objects`: client e frontend devono usare solo URL diretti `/storage/v1/object/public/...`.

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
