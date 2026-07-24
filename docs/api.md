# API Reference

## Endpoints

### Autenticazione

Il backend usa tre livelli di autenticazione:

| Tipo | Come funziona | Usato da |
|------|---------------|----------|
| **Utente autenticato** | JWT Supabase in header `Authorization: Bearer <token>` | Quasi tutti gli endpoint |
| **Admin** | JWT valido + ruolo `admin` risolto server-side dal profilo utente | `/admin/*` |
| **API key B2B** | Header `X-API-Key: <key>` | `GET /analytics/b2b` |

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
| `PATCH` | `/lists/{id}/items/{item_id}` | ✅ member | Aggiorna quantità, categoria o binding esplicito a un'offerta; con `pinned_offer_id` salva snapshot e categoria coerenti via RPC concorrente-safe `update_list_item` (`SECURITY INVOKER`, protetta da RLS + `auth.uid()`), poi rilegge item persistito |
| `DELETE` | `/lists/{id}/items/{item_id}` | ✅ member | Rimuove item via RPC concorrente-safe `remove_list_item` (`SECURITY INVOKER`, `search_path` fissato a `public`) |
| `POST` | `/lists/{id}/items/{item_id}/toggle` | ✅ member | Check/uncheck item; registra `checked_by`, `checked_at` |
| `POST` | `/lists/{id}/invite` | ✅ owner | Crea link invito (token 64 char, TTL 7 giorni) |
| `GET` | `/lists/{id}/members` | ✅ member | Lista membri lista condivisa con campi flatten `display_name`, `avatar_url` ed `email` pronti per UI |
| `DELETE` | `/lists/{id}/members/{user_id}` | ✅ owner/member(self) | Owner rimuove un altro membro oppure un member lascia la lista da solo; la vista torna sulla lista owner implicita e viene notificata solo la parte interessata |
| `GET` | `/lists/{id}/deal-freshness` | ✅ member | Freshness di tutte le offerte pinnate nella lista; le offerte fuori raggio per il viewer corrente risultano `unavailable` con flag risposta `offer_visibility_status='hidden_for_viewer'`, senza esporre prezzo attuale |
| `POST` | `/lists/{id}/clear-stale-offers` | ✅ member | Pulisce `pinned_offer_id` e `found_deals` degli item con offerte `expired`/`unavailable` tramite la stessa RPC concorrente-safe `update_list_item` usata dalle patch item, cosi' lo stato persistito si riallinea dopo che le read API hanno gia' proiettato l'item sotto `Senza offerta` |

### Offerte (`/offers`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/offers` | ❌ | Offerte attive confermate, con ricerca per nome, filtri `category` e `supermarket_id`, paginazione e dati del supermercato |

Nota implementativa: `/offers` restituisce `{ items, nextPage, total }`. L'immagine estratta o caricata in review è la proprietà `image_url` dell'offerta.

### Volantini (`/flyers`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `GET` | `/flyers` | ✅ admin/manager | Lista flyer sorgente in review; i manager vedono solo flyer con almeno un target assegnato ai propri supermercati |
| `GET` | `/flyers/public` | ❌ | Lista volantini pubblici completati con almeno un'offerta confermata |
| `GET` | `/flyers/{flyer_id}` | ✅ admin/manager | Dettaglio singolo volantino |
| `PATCH` | `/flyers/{flyer_id}` | ✅ admin/manager | Aggiorna `valid_from`/`valid_to` del flyer sorgente e propaga le stesse date a tutte le offerte collegate |
| `GET` | `/flyers/{flyer_id}/targets` | ✅ admin/manager | Legge i supermercati target di un flyer sorgente |
| `PUT/PATCH` | `/flyers/{flyer_id}/targets` | ✅ admin/manager | Aggiorna i supermercati target; prima della conferma salva lo staging, dopo la conferma riconcilia flyer e offerte pubbliche `published_target` |

Contratto di conferma:

- `POST /flyers/{flyer_id}/offers/confirm` conferma sempre le offerte del flyer sorgente come `source_master`.
- La visibilita' pubblica su `/offers` e `/flyers/public` dipende dalle offerte confermate `published_target`.
- La conferma deve quindi essere idempotente: se una pubblicazione si interrompe dopo aver confermato il source ma prima di aver clonato tutto, rilanciare `confirm` deve completare i `published_target` mancanti e riallineare `products_count` del flyer pubblico al numero reale di offerte pubblicate.
- Dopo la conferma, `PUT/PATCH /flyers/{flyer_id}/targets` mantiene lo stesso contratto materiale: target aggiunti creano/upsertano flyer pubblici e offerte `published_target`, target rimossi eliminano le offerte pubbliche e il flyer clone di quel supermercato.
| `POST` | `/flyers/upload-url` | ✅ admin/manager | Crea URL/token firmato per caricare direttamente nel bucket privato `flyers` senza passare il file dalla Function frontend; valida tipo, dimensione dichiarata e target manager/admin |
| `POST` | `/flyers/upload/complete` | ✅ admin/manager | Valida oggetto Storage caricato (PDF/JPG/PNG/WebP, max 50 MB), calcola hash server-side, controlla duplicati e crea un solo flyer `status='pending'` + righe `flyer_targets` |
| `POST` | `/flyers/{flyer_id}/extract` | ✅ admin/manager | Avvia estrazione AI per un volantino `pending` oppure riprende dal prossimo chunk non ancora completato quando esiste progresso PDF persistito (`status='error'` o retry manuale dopo failure transiente) |
| `GET` | `/flyers/{flyer_id}/draft-offers` | ✅ admin/manager | Lista offerte estratte ma non confermate |
| `PATCH` | `/flyers/{flyer_id}/draft-offers/{offer_id}` | ✅ admin/manager | Modifica inline dei campi e dell'immagine della draft offer |
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
- Gli endpoint che restituiscono offerte (`/offers`, `/flyers/{flyer_id}/draft-offers`) espongono anche `unit_price_value`, `unit_price_unit`, `unit_price_label`.
- `GET /flyers/{flyer_id}/draft-offers` espone `image_url` con precedenza `draft_image_url -> products.image_url`, così la review mostra l'immagine staged anche prima della creazione del prodotto canonico.
- In review, validità offerte è flyer-scoped: creazione manuale bozza eredita sempre `flyers.valid_from`/`flyers.valid_to`, `PATCH /flyers/{flyer_id}/draft-offers/{offer_id}` non modifica più le date, e `PATCH /flyers/{flyer_id}` aggiorna l'intero set estratto.
- Dopo la conferma finale esistono due livelli di offerte:
  - righe sorgente `offers.offer_kind='source_master'` sul flyer `flyer_kind='source'`, usate come master admin per review/edit/delete
  - cloni pubblici `offers.offer_kind='published_target'` su flyer `flyer_kind='published_target'`, uno per supermercato target
- Ogni clone pubblico salva `source_offer_id` verso la riga `source_master` da cui deriva. Re-run della conferma e PATCH/DELETE su offerte confermate devono aggiornare o rimuovere i cloni esistenti, non duplicarli.
- Gli endpoint customer-facing (`/offers`) e le policy RLS pubbliche considerano offerte reali solo le righe `offer_kind='published_target'`.

### Contratto formato prodotto

- `format` non è più una stringa: è un JSON strutturato salvato sulle righe `offers`.
- Ogni offerta salva:
  - `format`: oggetto canonico compatto, senza campi `null` o default inutili
  - `format_key`: chiave canonica deterministica derivata da `format`
  - `format_label`: label leggibile derivata da `format`
- Identità prodotto canonico: `name + brand`.
- `format_label` è solo display/search aid. Non definisce unicità.
- Le API pubbliche e admin restituiscono sempre sia `format` sia `format_label`.
- Le response admin ricostruiscono `format_label` da `format` quando il valore salvato è vuoto, così bozze già strutturate ma con label stale restano leggibili in review.
- Le API admin e draft-offer accettano solo `format` strutturato. Il backend rifiuta `format` testuale legacy.
- `format.tipo="confezione_singola"` può rappresentare confezioni senza peso o volume noto. Quando disponibile, usare `peso_volume` + `unita_misura`; per confezioni contabili usare `num_pezzi`; se il dato non è presente, mantenere solo `tipo`.
- Il provider LLM deve emettere un `format` strutturato sparso: solo `tipo` e campi pertinenti, senza `null` superflui. Il backend resta source of truth per canonicalizzazione e compattazione.
- `format.varianti` è consentito solo in input estrazione LLM: il backend lo espande in prodotti/offerte distinti prima dell'upsert. Nessun prodotto persistito rappresenta un parent con varianti miste.
- L'estrazione deduplica le offerte su chiave del flyer e formato, senza matching verso un catalogo.
- In review il reviewer può caricare o sostituire l'immagine dell'offerta; alla conferma resta in `offers.image_url`.
- Per PDF multipagina il backend divide il file in chunk PDF rigidi da 3 pagine e invia un chunk per volta a Gemini. Dopo ogni chunk riuscito persiste subito le draft offers di quel chunk e aggiorna `flyers.extraction_metadata` con pagina corrente, percentuale, `last_completed_chunk` e `next_chunk_*`, così il frontend può mostrare avanzamento live durante il polling e review parziale.
- Se un chunk fallisce dopo i retry, il flyer passa a `status='error'`, ma le draft offers dei chunk già riusciti restano salvate. `flyers.extraction_metadata` espone `resume_available`, `failed_chunk_*`, `next_chunk_*` e `partial_products_count`; una nuova `POST /flyers/{flyer_id}/extract` riparte dal primo chunk non completato correttamente senza duplicare le offerte già persistite. La ripresa si basa su `extraction_metadata` persistito, non sullo `status` transitorio del flyer mentre il retry è già tornato a `processing`.
- Anche un failure runtime generico dopo almeno un chunk già persistito (per esempio errori transienti `httpx`/Supabase durante polling, review o altri accessi concorrenti) deve lasciare un resume point valido: `next_chunk_*` resta fonte di verità, `resume_available` viene rialzato e il retry successivo riparte dal prossimo chunk salvato invece di rieseguire il chunk 1.
- Se il processo web viene riavviato mentre il flyer è ancora `processing`, il backend deve trattare un record stale con `last_completed_chunk` + `next_chunk_*` come resumable anche senza transizione preventiva a `error`: lo stesso `POST /flyers/{flyer_id}/extract` deve poter riagganciare il checkpoint e riprendere dal chunk successivo.
- Allo startup del backend, un recovery pass scansiona i flyer rimasti `processing` dopo il crash/riavvio dell'istanza precedente. Se trova `last_completed_chunk` + `next_chunk_*`, marca il flyer come recoverable e mette automaticamente in coda un nuovo `ExtractionService().run(...)`; se invece il crash e' avvenuto prima del primo checkpoint, il flyer viene chiuso in `error` con messaggio esplicito e senza retry automatico.
- Se tutti i chunk risultano già persistiti e il failure arriva solo in coda (per esempio su update finale o side effect post-successo), il flyer non deve tornare a `status='error'`: il backend deve consolidarlo a `done`, con `resume_available=false`, perché non esiste più alcun checkpoint utile da riprendere.
- Quando Gemini fallisce o va in retry, backend logga anche contesto strutturato se disponibile: tipo eccezione, `code`, `status`, `message`, HTTP status/body e request id. Stesso dettaglio finisce in `retry_errors` dentro `extraction_log`.

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

Le notifiche Web Push e native FCM condividono lo stesso payload `data`, incluso `kind`, `flyer_id`, `status`, `products_count` e `url`. Il frontend usa questi campi per aggiornare subito la cache e aprire il deep link corretto. Le notifiche native FCM includono `android.notification.icon = "ic_notification"` e `android.notification.color = "#1E7A45"` per forzare la preview Android brandizzata, oltre a `apns.payload.aps.sound = "default"` per attivare il suono standard di iOS quando le impostazioni del dispositivo lo consentono.
La conferma di un volantino accoda job `notification_jobs` idempotenti e non invia in modo sincrono a tutti gli utenti. Il worker drenato da APScheduler o da `/ops/cron/notifications` crea le notifiche `favorite_offer` e `flyer_published`, persistendo sempre lo storico in `app_notifications` prima di inviare Web Push/FCM. Quando più prodotti preferiti dello stesso utente compaiono nello stesso flyer, il backend aggiorna una sola notifica aggregata per quel `user_id + flyer_id`. Le notifiche `flyer_published` raggiungono utenti che vedono il punto vendita nel raggio configurato; se il supermercato è anche tra i preferiti, il titolo usa il nome del supermercato.
Il copy standard è `Nuovo volantino da <nome_supermercato>` con body `<numero_offerte> nuove offerte disponibili`. Il deep link `flyer_published` apre `/offerte?sort=published_at&scroll=offers&context_supermarket_id=<supermarket_id>`.

### Ops (`/ops`)

| Metodo | Path | Auth | Descrizione |
|--------|------|------|-------------|
| `POST` | `/ops/cron/daily-maintenance` | Header `X-Ops-Secret` | Esegue cleanup offerte di flyer scaduti e rimozione item acquistati scaduti; se uno step fallisce risponde `status=partial_error` con array `errors` e continua gli altri cleanup; usato dal workflow GitHub schedulato |
| `POST` | `/ops/cron/notifications` | Header `X-Ops-Secret` | Drena i job `notification_jobs` pendenti con retry e risposta `{claimed, processed, failed}`; usabile da cron esterno oltre allo scheduler interno |

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

---
