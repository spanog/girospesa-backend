# GiroSpesa — Backend

FastAPI backend per GiroSpesa. Contiene tutta la logica di business dell'applicazione: ottimizzazione lista della spesa, gestione liste condivise, geocoding indirizzi, push notification, analytics B2B e gestione del catalogo prodotti.

> **Estrazione AI volantini:** questo repo contiene runtime, review flow e CLI di valutazione. Il vecchio workspace di estrazione è stato assorbito in questo backend.

## Deploy produzione

Per minimizzare costo e complessita', questo repo ora e' pronto per deploy automatico su Render Free tramite GitHub:

- `render.yaml` descrive il servizio web FastAPI (`uvicorn main:app --host 0.0.0.0 --port $PORT`)
- `.github/workflows/ci.yml` esegue la suite backend ad ogni PR
- `.github/workflows/render-keepalive.yml` invia un ping a `/health` ogni 10 minuti per ridurre il rischio di spin down su Render Free
- `.github/workflows/daily-maintenance.yml` esegue ogni giorno una manutenzione remota compatibile con il free tier
- `.github/workflows/supabase-db-production.yml` applica automaticamente le migration Supabase quando `supabase/**` viene mergiato su `main`

### Variabili Render richieste

Imposta nel servizio Render:

- `ENVIRONMENT=production`
- `FRONTEND_URL=https://www.girospesa.it` oppure dominio frontend reale
- `BACKEND_URL=https://api.girospesa.it` oppure dominio backend reale
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `APP_SESSION_SECRET`
- `DB_DSN`
- `GOOGLE_API_KEY`
- `GEMINI_MODEL`
- `VAPID_PRIVATE_KEY`
- `VAPID_PUBLIC_KEY`
- `WEBHOOK_SECRET`
- `OPS_CRON_SECRET`

### GitHub Actions secrets richiesti

Nel repo GitHub backend configura:

- `BACKEND_HEALTHCHECK_URL`
- `BACKEND_DAILY_MAINTENANCE_URL`
- `OPS_CRON_SECRET`
- `SUPABASE_ACCESS_TOKEN`
- `SUPABASE_DB_PASSWORD`
- `SUPABASE_PROJECT_ID`

Esempio:

```text
BACKEND_HEALTHCHECK_URL=https://api.girospesa.it/health
BACKEND_DAILY_MAINTENANCE_URL=https://api.girospesa.it/ops/cron/daily-maintenance
```

Per le migration Supabase:

```text
SUPABASE_PROJECT_ID=<project-ref produzione>
SUPABASE_DB_PASSWORD=<database password produzione>
SUPABASE_ACCESS_TOKEN=<personal access token Supabase>
```

Note deploy database:

- Il deploy Render del backend non applica migration Supabase.
- Lo schema production si aggiorna tramite `.github/workflows/supabase-db-production.yml`.
- Il workflow parte su push a `main` quando cambia `supabase/**`, ed e' lanciabile anche a mano con `workflow_dispatch`.
- Primo setup consigliato: lanciare una volta il workflow manualmente dopo aver configurato i secret, cosi' verifichi che baseline e credenziali siano corrette prima del prossimo merge.

### Nota importante sul piano free

Render dichiara che i servizi Free non sono pensati per produzione stabile, vanno in spin down dopo inattivita' e possono essere riavviati in qualsiasi momento. Per questo il backend mantiene APScheduler locale, ma la pulizia giornaliera in produzione viene anche richiamata da GitHub Actions tramite `POST /ops/cron/daily-maintenance`, cosi' i cleanup non dipendono dal fatto che il container sia sveglio a mezzanotte.

Per ridurre il rischio di idle durante il giorno, `.github/workflows/render-keepalive.yml` esegue anche un `curl` a `BACKEND_HEALTHCHECK_URL` ogni 10 minuti. Questo mitiga il cold start del piano Free, ma non offre garanzia forte come un piano paid: GitHub Actions schedulato puo' accumulare ritardi e Render puo' comunque riavviare l'istanza.

Il workflow `.github/workflows/daily-maintenance.yml` usa `curl --fail-with-body`, cosi' eventuali errori HTTP mantengono il body nei log GitHub. L'endpoint `/ops/cron/daily-maintenance` resta best-effort: se un singolo step interno fallisce, la risposta segnala `status=partial_error` e il nome degli step falliti in `errors`, ma gli altri cleanup continuano.

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

Il frontend web usa Supabase Auth con `@supabase/ssr`: browser, proxy Next.js e Server Components condividono la sessione tramite i cookie SSR di Supabase, mentre le chiamate autenticate a questo backend inoltrano sempre il token accesso Supabase in `Authorization: Bearer <token>`. Android/iOS dovranno seguire lo stesso contratto bearer. Il backend valida i bearer token utente esclusivamente tramite le signing keys/JWKS di Supabase e usa `SUPABASE_SECRET_KEY` per tutte le operazioni server-side privilegiate su Auth, Database e Storage. Non esiste più un cookie sessione backend per auth applicativa o stream SSE.

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

## Endpoint API

### Autenticazione

Il backend usa tre livelli di autenticazione:

| Tipo | Come funziona | Usato da |
|------|---------------|----------|
| **Utente autenticato** | JWT Supabase in header `Authorization: Bearer <token>` | Quasi tutti gli endpoint |
| **Admin** | JWT valido + ruolo `admin` risolto server-side dal profilo utente | `/admin/*` |
| **API key B2B** | Header `X-API-Key: <key>` | `GET /analytics/b2b` |
| **Webhook secret** | Header `X-Webhook-Secret: <secret>` | `POST /push/notify-favorites` |

### Utenti (`/users`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/users/me` | ✅ | Profilo utente autenticato, incluse preferenze notifiche granulari effettivamente usate |
| `PUT` | `/users/me` | ✅ | Aggiorna profilo; auto-geocode se cambia indirizzo e salva preferenza unica notifiche (`notifications_enabled`) |
| `POST` | `/users/geocode` | ✅ | Geocodifica indirizzo di casa → aggiorna `home_lat/lng` e `home_location` PostGIS |
| `POST` | `/users/me/avatar` | ✅ | Upload avatar (JPEG/PNG/WebP, max 5 MB) → bucket `avatars` |
| `DELETE` | `/users/me` | ✅ | Elimina account + dati collegati; risponde `204` |

### Contatti pubblici (`/contact-requests`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/contact-requests` | ❌ / opzionale | Endpoint `multipart/form-data` unico per `bug_report`, `collaboration_request` e `missing_flyer_request`; i bug report inviano sempre email via SMTP e, se presenti, allegano direttamente al messaggio fino a 3 screenshot `PNG/JPEG` |

### Lista spesa (`/lists`)

Ogni account possiede una sola lista owner stabile, ma puo' anche partecipare a piu' liste condivise. `GET /lists` restituisce il workspace owner piu' tutte le liste condivise visibili; `POST /lists/select` salva su `user_profiles.active_list_id` quale workspace l'utente sta usando in questo momento. `GET /lists/active` risolve quindi prima la lista selezionata se ancora accessibile, poi la lista owner come fallback, e infine una lista condivisa ancora visibile se necessario. Quando un invito viene accettato, la lista condivisa appena ricevuta diventa attiva; quando l'utente esce da una condivisione o viene rimosso, il backend riallinea la selezione alla lista owner. Le notifiche `list_member_removed` e `list_member_left` mostrano l'identita' dell'attore come `Nome Cognome (email)` quando l'email e' disponibile, e mantengono anche i campi strutturati nel payload per eventuale rendering dedicato. Condivisione e gestione inviti restano owner-only tramite inviti email diretti: solo il proprietario puo' creare inviti, elencare inviti pendenti e revocarli; un membro condiviso puo' solo leggere i membri della lista e lasciare la propria membership.
Per sync quasi immediato tra membri, il backend espone anche `GET /lists/{list_id}/events` come stream `text/event-stream`: ogni mutazione lista/membership/invite pubblica un `pg_notify` su canale Postgres dedicato, lo stream inoltra solo eventi della lista sottoscritta e il frontend invalida le query locali senza refresh manuale.

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/lists` | ✅ | Elenca tutti i workspace visibili all'utente: unica lista owner protetta + eventuali liste condivise, con metadati `is_active`, `is_owner`, `member_role`, `owner_display_name`, contatori e ordinamento pronto per il selector frontend |
| `POST` | `/lists/select` | ✅ | Imposta la lista attiva corrente scegliendo uno dei workspace visibili all'utente e persiste `active_list_id` |
| `GET` | `/lists/active` | ✅ | Lista attiva; auto-crea la lista owner `La mia lista` se non esiste; arricchisce gli item con `brand`, `category` e `subcategory`, facendo backfill del brand da prodotto/offerta quando lo snapshot storico non lo contiene. Il nome risposta è già viewer-specifico: owner vede `La mia lista`, i membri vedono `La lista di <owner>`. Per liste condivise, il pin offerta resta canonico nel DB ma la risposta maschera prezzo/supermercato quando il viewer non vede quel supermercato nel proprio raggio attivo |
| `GET` | `/lists/{id}/events` | ✅ member | Stream SSE autenticato con `Authorization: Bearer <token>`; inoltra eventi `list_updated`, `members_updated`, `invites_updated` per sync live della lista condivisa |
| `POST` | `/lists/{id}/reset` | ✅ member | Svuota la lista corrente dopo conferma frontend e restituisce la lista aggiornata |
| `POST` | `/lists/{id}/items/remove-purchased` | ✅ member | Rimuove in blocco dalla lista solo gli item acquistati e restituisce la lista aggiornata, senza cancellare `purchase_history` |
| `POST` | `/lists/{id}/items` | ✅ member | Aggiunge item (manuale o da offerta) e salva snapshot `brand`/`category`/`subcategory` quando collegato a prodotto/offerta; persistenza via RPC concorrente-safe `append_list_item` (`SECURITY INVOKER`, `search_path` fissato a `public`) |
| `PATCH` | `/lists/{id}/items/{item_id}` | ✅ member | Aggiorna quantità o binding esplicito a un'offerta; con `pinned_offer_id` salva `source`, `pinned_product_id`, `found_deals`, categoria e sottocategoria coerenti via RPC concorrente-safe `update_list_item` (`SECURITY INVOKER`, protetta da RLS + `auth.uid()`), poi rilegge item persistito |
| `DELETE` | `/lists/{id}/items/{item_id}` | ✅ member | Rimuove item via RPC concorrente-safe `remove_list_item` (`SECURITY INVOKER`, `search_path` fissato a `public`) |
| `POST` | `/lists/{id}/items/{item_id}/toggle` | ✅ member | Check/uncheck item; registra `checked_by`, `checked_at` |
| `POST` | `/lists/{id}/invite` | ✅ owner | Crea link invito (token 64 char, TTL 7 giorni) |
| `GET` | `/lists/{id}/members` | ✅ member | Lista membri lista condivisa con campi flatten `display_name`, `avatar_url` ed `email` pronti per UI |
| `DELETE` | `/lists/{id}/members/{user_id}` | ✅ owner/member(self) | Owner rimuove un altro membro oppure un member lascia la lista da solo; la vista torna sulla lista owner implicita e viene notificata solo la parte interessata |
| `GET` | `/lists/{id}/deal-freshness` | ✅ member | Freshness di tutte le offerte pinnate nella lista; le offerte fuori raggio per il viewer corrente risultano `unavailable` con flag risposta `offer_visibility_status='hidden_for_viewer'`, senza esporre prezzo attuale |
| `POST` | `/lists/{id}/clear-stale-offers` | ✅ member | Pulisce `pinned_offer_id` e `found_deals` degli item con offerte `expired`/`unavailable` tramite la stessa RPC concorrente-safe `update_list_item` usata dalle patch item, cosi' lo stato persistito si riallinea dopo che le read API hanno gia' proiettato l'item sotto `Senza offerta` |

### Prodotti e offerte (`/products`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/products` | ❌ | Offerte attive con ricerca `q` ibrida (`word_similarity` + match per prefisso/sottostringa), filtro esatto `product_id` sul prodotto canonico, filtri `category`, `supermarket` (slug compat legacy), `supermarket_id` (punto vendita esatto) oppure `supermarket_ids=<uuid>&supermarket_ids=<uuid>` per multi-store nativo, ordinamento default per nome prodotto, `sort=expiry` per scadenza crescente, `expiring_soon=true` per offerte che scadono entro 3 giorni, paginazione |
| `GET` | `/products/{id}` | ❌ | Dettaglio singola offerta (prodotto + supermercato) |
| `GET` | `/products/{id}/similar` | ❌ | Altre offerte attive per lo stesso prodotto canonico (ordinate per prezzo) |

Nota implementativa: `/products` restituisce sempre `{ items, nextPage, total?, supermarket_count?, expiring_soon_count?, counts_by_supermarket_id?, counts_by_supermarket_slug? }`. I due dizionari `counts_by_supermarket_*` sono pensati per badge/cards supermercati su `/offerte` e `/volantini` senza più N+1 chiamate client. Il filtro `expiring_soon=true` usa stessa finestra temporale del contatore `expiring_soon_count`: `valid_to` compreso tra oggi e oggi + 3 giorni. La ricerca `q` passa da `public.search_products_catalog`, che mantiene ranking fuzzy con `word_similarity` ma include anche match per prefisso e sottostringa su nome/brand, così query come `mozza` trovano `Mozzarella` senza perdere tolleranza ai refusi. `product_id=<canonical-products.id>` restringe invece il listing a tutte le offerte pubbliche attive di quel prodotto canonico. Per compatibilità storica `supermarket=<slug>` continua a risolvere la prima insegna corrispondente, ma i filtri frontend per singolo store devono usare `supermarket_id=<uuid>` e quelli multi-store `supermarket_ids[]=...` per distinguere filiali con lo stesso `slug`.

### Preferiti (`/favorites`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/favorites` | ✅ | Lista preferiti con `active_offers[]` ordinato per prezzo, `best_offer` come primo elemento e metadati supermercato (`name`, `logo_url`, `address`) per ogni offerta |
| `GET` | `/favorites/{product_id}` | ✅ | Controlla se un prodotto è tra i preferiti |
| `POST` | `/favorites` | ✅ | Aggiunge ai preferiti (body: `{product_id}`) |
| `DELETE` | `/favorites/{product_id}` | ✅ | Rimuove dai preferiti |

### Volantini (`/flyers`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/flyers` | ✅ admin/manager | Lista flyer sorgente in review; i manager vedono solo flyer con almeno un target assegnato ai propri supermercati |
| `GET` | `/flyers/public` | ❌ | Lista volantini pubblici completati con almeno un'offerta confermata |
| `GET` | `/flyers/{flyer_id}` | ✅ admin/manager | Dettaglio singolo volantino |
| `PATCH` | `/flyers/{flyer_id}` | ✅ admin/manager | Aggiorna `valid_from`/`valid_to` del flyer sorgente e propaga le stesse date a tutte le offerte collegate |
| `GET` | `/flyers/{flyer_id}/targets` | ✅ admin/manager | Legge i supermercati target di un flyer sorgente |
| `PUT/PATCH` | `/flyers/{flyer_id}/targets` | ✅ admin/manager | Aggiorna i supermercati target prima della conferma finale |

Contratto di conferma:

- `POST /flyers/{flyer_id}/offers/confirm` conferma sempre le offerte del flyer sorgente come `source_master`.
- La visibilita' pubblica su `/products` e `/flyers/public` dipende invece dai cloni `published_target`.
- La conferma deve quindi essere idempotente: se una pubblicazione si interrompe dopo aver confermato il source ma prima di aver clonato tutto, rilanciare `confirm` deve completare i `published_target` mancanti e riallineare `products_count` del flyer pubblico al numero reale di offerte pubblicate.
| `POST` | `/flyers/upload` | ✅ admin/manager | Upload volantino sorgente (PDF/JPG/PNG/WebP, max 50 MB) con uno o piu `supermarket_ids`; crea un solo flyer `status='pending'` + righe `flyer_targets` |
| `POST` | `/flyers/{flyer_id}/extract` | ✅ admin/manager | Avvia estrazione AI per un volantino `pending` oppure riprende dal prossimo chunk non ancora completato quando esiste progresso PDF persistito (`status='error'` o retry manuale dopo failure transiente) |
| `GET` | `/flyers/{flyer_id}/draft-offers` | ✅ admin/manager | Lista offerte estratte ma non confermate |
| `PATCH` | `/flyers/{flyer_id}/draft-offers/{offer_id}` | ✅ admin/manager | Modifica inline di una draft offer; `detach_product=true` rimuove il binding catalogo senza creare prodotti |
| `POST` | `/flyers/{flyer_id}/draft-offers/{offer_id}/image` | ✅ admin/manager | Upload immagine prodotto staged per una bozza non agganciata; salva `draft_image_url` fino alla conferma |
| `POST` | `/flyers/{flyer_id}/offers/confirm` | ✅ admin/manager | Conferma le draft del flyer sorgente, crea/upserta i prodotti canonici mancanti, marca le righe sorgente come `source_master`, poi materializza/upserta un volantino pubblico distinto e un set di offerte `published_target` distinto per ogni supermercato target |
| `POST` | `/flyers/admin/cleanup` | 👑 admin | Trigger manuale pulizia volantini scaduti (eseguita automaticamente ogni mezzanotte) |

`GET /flyers` non accetta flag client come `admin`, `manager` o `role`: autorizzazione e scoping dei risultati dipendono esclusivamente dal JWT/sessione validati lato backend. Per i flyer sorgente, `GET /flyers` e `GET /flyers/{flyer_id}` espongono anche `draft_count`, `confirmed_count` e `published_target_count`, così la dashboard admin distingue correttamente tra "Da confermare" e "Elaborato" senza usare `is_public` del source flyer.

### Contratto prezzi estrazione

- Il backend accetta sia il prompt legacy (`price_offer`, `category`, `subcategory`) sia il prompt v2 (`price_current`, `category_main`, `category_sub`, `discount_percentage`, `price_per_unit`, `price_per_unit_measure`).
- `price_original` resta il prezzo pieno/non in offerta solo se stampato sul volantino. Non viene mai inferito.
- `offers` salva anche il prezzo unitario strutturato:
  - `unit_price_value NUMERIC(8,2)`
  - `unit_price_unit TEXT` con valori ammessi `kg`, `L`, `kg sgocc`
  - `unit_price TEXT` come label derivata per compatibilità
- Gli endpoint che restituiscono offerte (`/products`, `/flyers/{flyer_id}/draft-offers`, `/favorites`, `/optimize`) espongono anche `unit_price_value`, `unit_price_unit`, `unit_price_label`.
- `GET /flyers/{flyer_id}/draft-offers` espone `image_url` con precedenza `draft_image_url -> products.image_url`, così la review mostra l'immagine staged anche prima della creazione del prodotto canonico.
- In review, validità offerte è flyer-scoped: creazione manuale bozza eredita sempre `flyers.valid_from`/`flyers.valid_to`, `PATCH /flyers/{flyer_id}/draft-offers/{offer_id}` non modifica più le date, e `PATCH /flyers/{flyer_id}` aggiorna l'intero set estratto.
- Dopo la conferma finale esistono due livelli di offerte:
  - righe sorgente `offers.offer_kind='source_master'` sul flyer `flyer_kind='source'`, usate come master admin per review/edit/delete
  - cloni pubblici `offers.offer_kind='published_target'` su flyer `flyer_kind='published_target'`, uno per supermercato target
- Ogni clone pubblico salva `source_offer_id` verso la riga `source_master` da cui deriva. Re-run della conferma e PATCH/DELETE su offerte confermate devono aggiornare o rimuovere i cloni esistenti, non duplicarli.
- Gli endpoint customer-facing (`/products`, `/favorites`, `/optimize`) e le policy RLS pubbliche considerano offerte reali solo le righe `offer_kind='published_target'`.

### Contratto formato prodotto

- `format` non è più una stringa: è un JSON strutturato salvato sulle righe `offers`.
- Ogni offerta salva:
  - `format`: oggetto canonico compatto, senza campi `null` o default inutili
  - `format_key`: chiave canonica deterministica derivata da `format`
  - `format_label`: label leggibile derivata da `format`
- Identità prodotto canonico: `name + brand`.
- `format_label` è solo display/search aid. Non definisce unicità.
- Le API pubbliche e admin restituiscono sempre sia `format` sia `format_label`.
- Le API admin e draft-offer accettano solo `format` strutturato. Il backend rifiuta `format` testuale legacy.
- Il provider LLM deve emettere un `format` strutturato sparso: solo `tipo` e campi pertinenti, senza `null` superflui. Il backend resta source of truth per canonicalizzazione e compattazione.
- `format.varianti` è consentito solo in input estrazione LLM: il backend lo espande in prodotti/offerte distinti prima dell'upsert. Nessun prodotto persistito rappresenta un parent con varianti miste.
- Matching fuzzy/optimizer usa `name`, `brand`, `format_label`; mai JSON raw.
- Durante l'estrazione il backend deduplica prima in memoria su `(name, brand)`, cerca match fuzzy nel catalogo esistente per agganciare le bozze quando possibile, deduplica le offerte su `(flyer_id, draft_product_key, format_key)` e fa upsert idempotente delle draft offers. Non crea nuovi prodotti canonici finché le offerte restano in bozza; li crea/upserta solo alla conferma finale.
- In review il reviewer può caricare un'immagine prodotto solo per bozze `new_on_confirm` (incluse bozze sganciate manualmente dal catalogo). Alla conferma, `draft_image_url` viene copiato in `products.image_url`; le bozze già agganciate a un prodotto esistente non possono modificare l'immagine catalogo da questa pagina.
- Per PDF multipagina il backend divide il file in chunk PDF rigidi da 3 pagine e invia un chunk per volta a Gemini. Dopo ogni chunk riuscito persiste subito le draft offers di quel chunk e aggiorna `flyers.extraction_metadata` con pagina corrente, percentuale, `last_completed_chunk` e `next_chunk_*`, così il frontend può mostrare avanzamento live durante il polling e review parziale.
- Se un chunk fallisce dopo i retry, il flyer passa a `status='error'`, ma le draft offers dei chunk già riusciti restano salvate. `flyers.extraction_metadata` espone `resume_available`, `failed_chunk_*`, `next_chunk_*` e `partial_products_count`; una nuova `POST /flyers/{flyer_id}/extract` riparte dal primo chunk non completato correttamente senza duplicare le offerte già persistite. La ripresa si basa su `extraction_metadata` persistito, non sullo `status` transitorio del flyer mentre il retry è già tornato a `processing`.
- Anche un failure runtime generico dopo almeno un chunk già persistito (per esempio errori transienti `httpx`/Supabase durante polling, review o altri accessi concorrenti) deve lasciare un resume point valido: `next_chunk_*` resta fonte di verità, `resume_available` viene rialzato e il retry successivo riparte dal prossimo chunk salvato invece di rieseguire il chunk 1.
- Se il processo web viene riavviato mentre il flyer è ancora `processing`, il backend deve trattare un record stale con `last_completed_chunk` + `next_chunk_*` come resumable anche senza transizione preventiva a `error`: lo stesso `POST /flyers/{flyer_id}/extract` deve poter riagganciare il checkpoint e riprendere dal chunk successivo.
- Allo startup del backend, un recovery pass scansiona i flyer rimasti `processing` dopo il crash/riavvio dell'istanza precedente. Se trova `last_completed_chunk` + `next_chunk_*`, marca il flyer come recoverable e mette automaticamente in coda un nuovo `ExtractionService().run(...)`; se invece il crash e' avvenuto prima del primo checkpoint, il flyer viene chiuso in `error` con messaggio esplicito e senza retry automatico.
- Se tutti i chunk risultano già persistiti e il failure arriva solo in coda (per esempio su update finale o side effect post-successo), il flyer non deve tornare a `status='error'`: il backend deve consolidarlo a `done`, con `resume_available=false`, perché non esiste più alcun checkpoint utile da riprendere.
- Quando Gemini fallisce o va in retry, backend logga anche contesto strutturato se disponibile: tipo eccezione, `code`, `status`, `message`, HTTP status/body e request id. Stesso dettaglio finisce in `retry_errors` dentro `extraction_log`.

### Ottimizzazione (`/optimize`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/optimize` | ✅ member | Ottimizza lista spesa → gruppi per supermercato con risparmio e alternative; accesso consentito solo ai membri della lista indicata. Il matching usa solo i supermercati visibili al viewer corrente secondo `search_*`/`home_*` + `max_distance_km`; un `pinned_offer_id` fuori raggio non viene restituito come default né incluso tra le alternative |

### Supermercati (`/supermarkets`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/supermarkets` | ❌ | Directory supermercati attivi, ordinati per nome; con `lat`, `lng`, `max_distance_km` restituisce solo quelli vicini con `distance_km` |

### Inviti (`/invite`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/lists/invites` | ✅ | Elenca inviti ricevuti dall'utente autenticato |
| `POST` | `/lists/invites/{invite_id}/accept` | ✅ | Accetta invito email e diventa membro della lista |

### Push notification (`/push`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/push/subscribe` | ✅ | Registra subscription Web Push del browser |
| `POST` | `/push/unsubscribe` | ✅ | Cancella subscription |
| `POST` | `/push/notify-favorites` | Webhook secret | Webhook Supabase: nuova offerta pubblica, confermata e attiva → aggiorna una singola `app_notifications.favorite_offer` per `utente + flyer` e invia Web Push agli utenti che hanno quel prodotto tra i preferiti |

Le notifiche Web Push di completamento/fallimento estrazione includono nel campo `data` anche `kind`, `flyer_id`, `status`, `products_count` e `url`. Il frontend usa questi campi per aggiornare subito la cache della gestione volantini e poi confermare lo stato tramite refetch HTTP.
Le notifiche `favorite_offer` restano guidate dal prodotto preferito, non da `preferred_supermarkets`: il supermercato preferito serve ai filtri customer, non al routing notifiche. In locale o in ambienti senza `WEBHOOK_SECRET`, la conferma volantino pubblica le stesse `favorite_offer` direttamente durante la creazione dei cloni `published_target`, così l'inbox non dipende dal solo webhook esterno. Quando più prodotti preferiti dello stesso utente compaiono nello stesso flyer, il backend aggiorna una sola notifica aggregata per quel `user_id + flyer_id` invece di generarne una per ogni offerta.

### Ops (`/ops`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/ops/cron/daily-maintenance` | Header `X-Ops-Secret` | Esegue cleanup offerte di flyer scaduti e rimozione item acquistati scaduti; se uno step fallisce risponde `status=partial_error` con array `errors` e continua gli altri cleanup; usato dal workflow GitHub schedulato |

### Acquisti (`/purchases`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/purchases/items/{item_id}` | ✅ | Segna item come acquistato; registra prezzo, risparmio e snapshot prodotto (brand, formato, immagine, categoria, unit price) e aggiorna i flag `purchased_*` tramite RPC `update_list_item` concorrente-safe |
| `DELETE` | `/purchases/items/{item_id}` | ✅ | Annulla acquisto; pulisce i flag `purchased_*` tramite RPC `update_list_item` e rimuove la riga da `purchase_history` |
| `GET` | `/purchases/history` | ✅ | Storico risparmio paginato (ultimi N giorni, default 90) con filtri server-side (`category`, `subcategory`, `supermarket`, `source`) e metadati visuali completi per la UI |

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

- `flyer_cleanup` runs daily at 00:00 Europe/Rome and deletes offers linked to expired flyers, while keeping flyer rows/files for admin history.
- `purchased_items_cleanup` runs daily at 00:00 Europe/Rome and removes purchased list items from previous Rome days, resetting the "Acquistati oggi" section automatically without touching purchase history.

### Note storico acquisti

- `purchase_history.product_id` resta valorizzabile come snapshot storico del prodotto acquistato, ma non mantiene più una foreign key verso `products`.
- `purchase_history.quantity` salva quantità acquistata; `price_paid`, `price_original` e `savings` nello storico sono importi totali già scalati per quantità.
- `purchase_history` salva anche snapshot di `brand`, `format_label`, `image_url`, `category`, `subcategory` e dei campi `unit_price*`, così lo storico frontend mantiene stessa densità informativa anche se catalogo o offerte cambiano nel tempo.
- Questo permette di eliminare prodotti canonici non più usati senza perdere coerenza nello storico acquisti.

| Job | Schedule | Service | Description |
|-----|----------|---------|-------------|
| `flyer_cleanup` | Daily at 00:00 Europe/Rome | `services/flyer_cleanup.py` | Deletes offers linked to flyers where `valid_to < today`, but keeps the flyer row and uploaded file for historical/admin consultation. Flyers with `valid_to = NULL` are never auto-cleaned. |
| `purchased_items_cleanup` | Daily at 00:00 Europe/Rome | `services/purchased_items_cleanup.py` | Removes from each shopping list all items already purchased on previous Rome days. Items still purchased today stay visible in "Acquistati oggi" until midnight. Purchase history is not deleted. |

To trigger cleanup manually (ops or testing):

```bash
curl -X POST http://localhost:8000/flyers/admin/cleanup \
  -H "Authorization: Bearer <admin-jwt>"
# {"deleted": N}
```

---

## Note schema e RLS

- `analytics_data` ed `extraction_log` sono tabelle interne. RLS resta abilitato con policy esplicite `deny all`; accesso e scrittura passano solo dal backend con `SUPABASE_SECRET_KEY`.
- `manager_supermarkets` e `flyer_targets` sono tabelle di coordinamento backend/admin per gestione multi-branch e pubblicazione volantini. Anche qui RLS resta attivo con policy esplicite `deny all`; lettura e scrittura passano dal backend con `SUPABASE_SECRET_KEY`.
- Le richieste contatto, bug, collaborazione e volantini mancanti passano da `POST /contact-requests` con invio email al webmaster. Non esiste più persistenza applicativa su tabella `flyer_requests`.
- Log estrazione canonico: `extraction_log`. Eventuali ambienti locali legacy con `scraping_log` vengono riallineati dalla migration di hardening.
- PostGIS è abilitato nello schema `extensions`. `supermarkets.location`, `user_profiles.home_location` e `user_profiles.search_location` sono `geography(Point, 4326)` indicizzate GiST; la RPC `nearby_supermarkets` usa `ST_DWithin` e `ST_Distance`.
- Gli helper RLS per le liste condivise vivono nello schema non esposto `private` (`private.is_list_member`, `private.is_list_owner`). Restano `SECURITY DEFINER` per evitare ricorsione nelle policy, ma non sono endpoint RPC pubblici su `/rest/v1/rpc/*`.
- Nelle policy RLS che usano helper Auth Supabase, preferire `(select auth.uid())` e `(select auth.jwt())` al posto della chiamata inline: stessa semantica, meno lavoro per riga, niente warning `auth_rls_initplan`.
- Il trigger DB di signup `public.handle_new_user()` crea `user_profiles` copiando `display_name` e campi indirizzo (`home_address`, `home_city`, `home_province`, `home_postal_code`) da `raw_user_meta_data`, poi crea la lista owner `La mia lista` e la relativa membership `owner`.

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
  L'MVP non espone UI di alternative o sostituzioni suggerite
  Filtra per distanza con PostGIS (`nearby_supermarkets`, `ST_DWithin`)
                    │
                    ▼
  Raggruppa item con offerta per supermercato
  Raggruppa item manuali senza offerta separatamente
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
    → match prodotti esistenti + upsert draft offers con is_confirmed=false
    → aggiorna status → 'done'
                         │
                         ▼
  Admin / manager:
    GET /flyers/{id}/draft-offers
    PATCH draft offers
    POST /flyers/{id}/offers/confirm (crea prodotti nuovi se necessari)
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
      check notifications_enabled flag
      → insert app_notifications.favorite_offer
      → fetch push_subscriptions
      → per ogni subscription:
            send_push_notification (VAPID, pywebpush)
            se 410 Gone → cancella subscription stale
```

### 4. Lista condivisa

```
  Proprietario: POST /lists/{id}/invites
    Backend: crea invito email diretto su list_invites
                    │
  Destinatario: apre /lista?panel=inviti
  Destinatario: POST /lists/invites/{invite_id}/accept
    Backend: inserisce list_members {role: 'member'}
    Backend: segna invite → accepted
                    │
  Ora entrambi vedono la lista in tempo reale
  (SSE backend su /lists/{id}/events, alimentato da Postgres NOTIFY)
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
- Python 3.14+
- Node.js 20+ (per il frontend separato)

### 2. Variabili d'ambiente

```bash
cp .env.example .env
cp .env.test.example .env.test
```

Valori locali canonici:

- `SUPABASE_URL=http://127.0.0.1:54321`
- `FRONTEND_URL=http://localhost:3000` come valore canonico; `http://127.0.0.1:3000` resta supportato in CORS per compatibilita' loopback
- `GEOCODING_PROVIDER=nominatim` in locale, così signup/profilo/seed admin riflettono comportamento reale durante sviluppo manuale
- `GOOGLE_API_KEY` richiesto solo se si vuole usare estrazione AI Gemini
- `WEBMASTER_EMAIL`, `MAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS` e `SMTP_USE_SSL` servono per i form pubblici `/contact-requests`
- in produzione attuale GiroSpesa usa `Brevo` come relay SMTP applicativo; `Aruba` resta il provider delle mailbox umane (`info@`, ecc.)
- `SUPABASE_SECRET_KEY` si copia da `supabase status -o env` oppure dall'output equivalente del bootstrap locale
- `ADMIN_EMAIL` e `ADMIN_PASSWORD` servono per seedare utente admin via API service-role
- `.env` backend deve contenere solo variabili lette da FastAPI; credenziali Docker/Supabase CLI come `POSTGRES_PASSWORD`, `ANON_KEY`, `JWT_SECRET` e `SERVICE_ROLE_KEY` non vanno copiate qui
- `.env.test` e' locale-only ed e' ignorato da Git; i test integration iniettano questi valori solo dentro processo `pytest`, puntando allo stack Docker isolato su porte `55421`/`55422`, poi ripristinano l'env della sessione a fine run
- `POST /auth/signup` logga sempre causa reale lato backend con stack trace, ma verso frontend restituisce solo messaggi sanitizzati: duplicato account -> `400 {"detail":"Registrazione non riuscita. Verifica i dati inseriti oppure accedi se hai già un account."}`; password/email non valide hanno copy dedicato; errori imprevisti restano generici

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

- Schema canonico: `supabase/migrations/*.sql`
- Baseline attiva iniziale: `supabase/migrations/20260617000000_initial_schema.sql`
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
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
.venv/bin/task dev-setup
python -m uvicorn main:app --reload --port 8000
```

- API locale: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Se usi Python < 3.14, boot fallisce subito con errore esplicito prima di caricare router/app.
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
python3.14 -m venv .venv
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
| Servizio esterno | SMTP server | No | Necessario solo per invio mail da `/contact-requests` |

---

## Testing

### Test unitari (nessuna infrastruttura)

```bash
pytest tests/unit -v
pytest tests/ -v --ignore=tests/integration   # tutto tranne integration
```

Questa suite include anche snapshot contract mirati per router/unit test. Gli snapshot JSON vivono in `tests/__snapshots__/` e devono restare leggibili: normalizzare UUID, token, timestamp e URL variabili prima del confronto, mantenendo assertion esplicite per regole di business critiche.

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

I contract snapshot di integrazione vivono in `tests/integration/__snapshots__/`. Servono a bloccare regressioni di shape JSON su `/favorites`, `/optimize`, `/invite`, `/lists/active` e route affini senza sostituire le assertion semantiche.

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
SUPABASE_SECRET_KEY=<local-secret-key>
FRONTEND_URL=http://localhost:3000
# `127.0.0.1:3000` resta supportato in sviluppo per compatibilita' loopback

# ── Gemini extraction (solo se usi estrazione AI) ---------------------------
LLM_PROVIDER=gemini
GOOGLE_API_KEY=<google-api-key>
GEMINI_MODEL=gemma-4-31b-it

# ── Servizi esterni opzionali in locale -------------------------------------
GEOCODING_PROVIDER=nominatim         # default locale: allinea sviluppo manuale a produzione
WEBMASTER_EMAIL=webmaster@example.com
MAIL_FROM=no-reply@girospesa.local
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_TLS=false
SMTP_USE_SSL=false

# ── Esempio produzione attuale (Brevo) --------------------------------------
# MAIL_FROM=info@girospesa.it
# WEBMASTER_EMAIL=info@girospesa.it
# SMTP_HOST=smtp-relay.brevo.com
# SMTP_PORT=2525
# SMTP_USERNAME=<brevo-smtp-access>
# SMTP_PASSWORD=<brevo-smtp-key>
# SMTP_USE_TLS=false
# SMTP_USE_SSL=false

# ── Web Push / webhook opzionali --------------------------------------------
VAPID_PRIVATE_KEY=
VAPID_PUBLIC_KEY=
VAPID_MAILTO=mailto:info@girospesa.it
WEBHOOK_SECRET=

# ── Copia da `supabase status -o env` ---------------------------------------
# SUPABASE_SECRET_KEY <- SERVICE_ROLE_KEY

Compatibilita' deploy:

- il backend usa come nome canonico `SUPABASE_SECRET_KEY`
- per retrocompatibilita' accetta anche `SUPABASE_SERVICE_ROLE_KEY`, utile se qualche provider o vecchio `.env` usa ancora quel nome
- il backend supporta sia le chiavi legacy JWT (`service_role`) sia le nuove chiavi opache `sb_secret_...`

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
| **SMTP provider** | Email transazionali / contatto pubblico | `MAIL_FROM` + `SMTP_*` | Backend runtime attuale usa SMTP diretto via `smtplib`; in produzione GiroSpesa usa `Brevo` come relay SMTP e `Aruba` solo per ricezione mailbox |
| **Web Push (VAPID)** | Notifiche browser | Coppia VAPID + `WEBHOOK_SECRET` | Standard W3C, nessun servizio proprietario |

### Retry policy Gemini

- I chunk PDF Gemini usano `MAX_RETRIES = 3`.
- Errori provider `503/UNAVAILABLE` usano backoff esponenziale lungo con jitter.
- Errori transient server-side `500/502/504` e `INTERNAL` usano backoff esponenziale dedicato con jitter, per evitare tre retry troppo ravvicinati quando il provider e' in stato instabile.
- Se i retry si esauriscono, il flyer passa a `error` con checkpoint di resume sul chunk fallito.

---

## Logging

- In locale e ambienti non-production, il backend logga a livello `INFO`.
- In produzione (`ENVIRONMENT=production`), il root logger sale a `WARNING`, mentre `uvicorn.access` viene ridotto per evitare rumore nei log applicativi.
- Gli errori dei contact form (`POST /contact-requests`) vengono loggati esplicitamente come `warning` per misconfigurazioni e `exception` per failure SMTP, cosi' i log `Render` mostrano la causa reale del `500`.

---

## Bucket Supabase Storage

| Bucket | Pattern path | Scopo | Max dimensione | Accesso |
|--------|-------------|-------|----------------|---------|
| `avatars` | `{user_id}.{jpg\|png\|webp}` | Foto profilo utente | 5 MB | URL pubblico |
| `flyers` | `{user_id}/{uuid}.{pdf\|jpg}` | Volantini caricati (pre-estrazione) | 50 MB | URL pubblico |
| `product-images` | `{product_id}/{uuid}.{ext}` o `draft-offers/{offer_id}/{uuid}.{ext}` | Immagini prodotti admin e immagini staged durante review flyer | — | URL pubblico |

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

Definizione inclusa nella baseline `supabase/migrations/20260617000000_initial_schema.sql`. I dati sono sempre anonimi e GDPR-compliant — nessuna informazione personale.
RLS resta abilitato anche su questa tabella; accesso previsto solo tramite backend/service role.
