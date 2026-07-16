# API Reference

## Endpoints

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
| `PUT` | `/users/me` | ✅ | Aggiorna profilo e auto-geocode se cambia indirizzo; `max_distance_km` accetta valori da 1 a 20 |
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
| `GET` | `/supermarkets` | ❌ | Directory supermercati attivi, ordinati per nome; con `lat`, `lng`, `max_distance_km` (massimo 20) restituisce solo quelli vicini con `distance_km` |

### Inviti (`/invite`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/lists/invites` | ✅ | Elenca inviti ricevuti dall'utente autenticato |
| `POST` | `/lists/invites/{invite_id}/accept` | ✅ | Accetta invito email e diventa membro della lista |

### Push notification (`/push`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/push/subscribe` | ✅ | Registra subscription Web Push del browser |
| `POST` | `/push/unsubscribe` | ✅ | Cancella subscription Web Push |
| `POST` | `/push/native/subscribe` | ✅ | Registra token FCM app mobile |
| `POST` | `/push/native/unsubscribe` | ✅ | Cancella token FCM app mobile |
| `DELETE` | `/push/subscriptions` | ✅ | Cancella tutte le subscription web/native dell'utente |
| `POST` | `/push/notify-favorites` | Webhook secret | Webhook Supabase: nuova offerta pubblica, confermata e attiva → aggiorna una singola `app_notifications.favorite_offer` per `utente + flyer` e invia Web Push agli utenti che hanno quel prodotto tra i preferiti |

Le notifiche Web Push e native FCM condividono lo stesso payload `data`, incluso `kind`, `flyer_id`, `status`, `products_count` e `url`. Il frontend usa questi campi per aggiornare subito la cache e aprire il deep link corretto. Le notifiche native FCM includono `android.notification.icon = "ic_notification"` e `android.notification.color = "#1E7A45"` per forzare la preview Android brandizzata, oltre a `apns.payload.aps.sound = "default"` per attivare il suono standard di iOS quando le impostazioni del dispositivo lo consentono.
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
| `GET` | `/admin/products/{id}` | 👑 admin | Dettaglio prodotto con le offerte pubbliche (`offer_kind='published_target'`) e il formato corrente di ciascuna offerta |
| `PATCH` | `/admin/products/{id}` | 👑 admin | Modifica prodotto |
| `POST` | `/admin/products/{id}/archive` | 👑 admin | Archivia prodotto (soft delete) |
| `POST` | `/admin/products/{id}/restore` | 👑 admin | Ripristina prodotto archiviato |
| `DELETE` | `/admin/products/{id}` | 👑 admin | Elimina definitivamente prodotto senza offerte collegate; rimuove anche i preferiti collegati |
| `POST` | `/admin/products/{id}/image` | 👑 admin | Upload immagine prodotto → bucket `product-images` |
| `PATCH` | `/admin/products/{id}/offers/{oid}` | 👑 admin | Modifica offerta pubblica, incluso `format` strutturato; backend ricalcola `format_key` e `format_label` |
| `DELETE` | `/admin/products/{id}/offers/{oid}` | 👑 admin | Elimina offerta |
---
