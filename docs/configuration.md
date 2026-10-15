# Configuration

## Environment Variables

```bash
# ── Local dev obbligatorio per backend boot ----------------------------------
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_SECRET_KEY=<local-secret-key>
FRONTEND_URL=http://localhost:3000
# `127.0.0.1:3000` resta supportato in sviluppo per compatibilita' loopback
CORS_EXTRA_ORIGINS=

# ── Gemini extraction (solo se usi estrazione AI) ---------------------------
LLM_PROVIDER=gemini
GOOGLE_API_KEY=<google-api-key>
GEMINI_MODEL=gemma-4-31b-it

# ── Servizi esterni opzionali in locale -------------------------------------
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

# ── Web Push / native push ---------------------------------------------------
VAPID_PRIVATE_KEY=
VAPID_PUBLIC_KEY=
VAPID_MAILTO=mailto:info@girospesa.it
FCM_ENABLED=false
FCM_PROJECT_ID=
FCM_CLIENT_EMAIL=
FCM_PRIVATE_KEY=
NOTIFICATION_DELIVERY_WORKERS=2
NOTIFICATION_DELIVERY_BATCH_SIZE=10
NOTIFICATION_JOB_LOCK_TIMEOUT_SECONDS=600
PUSH_DELIVERY_TIMEOUT_SECONDS=10

# ── Copia da `supabase status -o env` ---------------------------------------
# SUPABASE_SECRET_KEY <- SERVICE_ROLE_KEY
```

Compatibilita' deploy:

- il backend usa come nome canonico `SUPABASE_SECRET_KEY`
- per retrocompatibilita' accetta anche `SUPABASE_SERVICE_ROLE_KEY`, utile se qualche provider o vecchio `.env` usa ancora quel nome
- il backend supporta sia le chiavi legacy JWT (`service_role`) sia le nuove chiavi opache `sb_secret_...`

```bash
# ── Admin seed condiviso -----------------------------------------------------
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change-me
```

Seed admin da eseguire dopo setup locale o in deploy:

- `.venv/bin/python -m scripts.seed_admin`
- `.venv/bin/python -m scripts.seed_admin --check`

Flow identica in locale, test, prod: cambia solo valore env.

---

## External Services

| Servizio | Scopo | Configurazione | Note |
|----------|-------|----------------|------|
| **Google Gemini** | Estrazione AI volantini | `GOOGLE_API_KEY` + `GEMINI_MODEL` | Unica dipendenza esterna richiesta quando usi AI extraction |
| **SMTP provider** | Email transazionali / contatto pubblico | `MAIL_FROM` + `SMTP_*` | Backend runtime attuale usa SMTP diretto via `smtplib`; in produzione GiroSpesa usa `Brevo` come relay SMTP e `Aruba` solo per ricezione mailbox |
| **Web Push (VAPID)** | Notifiche browser | Coppia VAPID | Standard W3C, nessun servizio proprietario |
| **Delivery queue** | Notifiche pubblicazione | `NOTIFICATION_DELIVERY_*`, `PUSH_DELIVERY_TIMEOUT_SECONDS` | Batch da 10, 2 thread, deadline push 10 s, recupero lock dopo 10 min. |

### Retry policy Gemini

- I chunk PDF Gemini usano `MAX_RETRIES = 3`.
- I PDF ufficiali fino a 100 MB sono accettati dal backend; il bucket Storage `flyers` deve avere un limite coerente di almeno 100 MB. I PDF nuovi vengono inviati in chunk da 2 pagine, quindi la dimensione complessiva del PDF non usa il limite di 20 MB previsto per le immagini inviate inline. Ogni richiesta Gemini ha una deadline hard di 8 minuti: gira in un sottoprocesso terminabile che legge il solo chunk da file temporaneo, quindi una connessione TLS viva ma senza risposta viene chiusa senza duplicare il PDF completo in RAM e passa nel retry con backoff. Una ripresa conserva la dimensione chunk già checkpointata.
- Un volantino `processing` può essere ripreso soltanto dopo 90 minuti senza aggiornamenti: finestra superiore ai tentativi del chunk e ai controlli di copertura con deadline, per non avviare due estrazioni concorrenti sullo stesso checkpoint.
- Dopo un riavvio del backend, un volantino interrotto passa a `error` con il checkpoint disponibile. La ripresa è esplicita tramite `POST /flyers/{flyer_id}/extract`; il boot non avvia automaticamente worker concorrenti.
- Errori provider `503/UNAVAILABLE` usano backoff esponenziale lungo con jitter.
- Errori transient server-side `500/502/504` e `INTERNAL` usano backoff esponenziale dedicato con jitter, per evitare tre retry troppo ravvicinati quando il provider e' in stato instabile.
- Se i retry si esauriscono, il flyer passa a `error` con checkpoint di resume sul chunk fallito.

---

## Logging

- In locale e ambienti non-production, il backend logga a livello `INFO`.
- In produzione (`ENVIRONMENT=production`), il root logger sale a `WARNING`, mentre `uvicorn.access` viene ridotto per evitare rumore nei log applicativi.
- Il middleware di timing aggiunge sempre `X-Request-ID` e `Server-Timing: app;dur=...` alla risposta. Logga solo richieste oltre 1000 ms con route parametrica, status, durata, dimensione dichiarata e request-id; non registra query string, JWT, email, coordinate o URL Storage.
- Gli errori dei contact form (`POST /contact-requests`) vengono loggati esplicitamente come `warning` per misconfigurazioni e `exception` per failure SMTP, cosi' i log `Render` mostrano la causa reale del `500`.

---

## Provider di estrazione locale

`LLM_PROVIDER` accetta `gemini` (predefinito, API Google) oppure `ollama` (inferenza locale).
La configurazione di produzione resta su Gemini finché non viene modificata esplicitamente.

| Variabile | Default | Descrizione |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Indirizzo del server Ollama raggiungibile dal backend. |
| `OLLAMA_MODEL` | `gemma4:31b-mlx` | Tag di un modello con capacità visive già installato. |
| `OLLAMA_TIMEOUT_SECONDS` | `600` | Timeout HTTP in secondi, maggiore di zero. |
| `OLLAMA_CONTEXT_TOKENS` | `16384` | Budget di contesto, almeno 1024 token. |
| `OLLAMA_MAX_OUTPUT_TOKENS` | `8192` | Limite positivo per i token generati. |
| `OLLAMA_IMAGE_DPI` | `150` | Risoluzione di rasterizzazione PDF, da 72 a 300 DPI. |
| `OLLAMA_CHUNK_SIZE_PAGES` | `1` | Pagine per richiesta/checkpoint; accetta 1 o 2. Con `gemma4:26b-mlx` usare obbligatoriamente 1 nel flusso locale ordinario. |

Ollama riceve immagini e prompt tramite `/api/chat`, senza streaming e con output JSON.
Il backend richiede `think=false` e temperatura 0,1. Questi valori rendono il confronto
riproducibile a livello di configurazione, ma non garantiscono risposte identiche.
La quantizzazione e il motore di inferenza dipendono dal tag installato, non dal provider Python.
Vedere [la guida locale](local-development.md#estrazione-locale-con-ollama) per comandi e cambio modello.

## Coordinate dei ritagli

Il contratto del modello usa `packshot_bbox` come oggetto con `x_min`, `y_min`,
`x_max`, `y_max`, oppure `null` quando la localizzazione è incerta. X è orizzontale
(da sinistra), Y verticale (dall'alto); griglia normalizzata 0–1000. Ollama riceve
uno schema JSON che impone i campi nominati e rifiuta gli array nella risposta.
I campi nominati eliminano l'ambiguità dell'ordine, ma non garantiscono che il modello
individui il prodotto corretto.

Il backend normalizza le nuove estrazioni in `[x_min, y_min, x_max, y_max]` e salva
`offers.packshot_bbox_version=2`. Le righe storiche mantengono coordinate e immagini
originali, con versione 1 (`[y_min, x_min, y_max, x_max]`). Vengono convertite in X/Y
in lettura per ritagli pendenti e API delle offerte interattive. Le copie pubblicate
conservano la versione del sorgente. Nessun riordino automatico tenta di riparare
le coordinate miste già prodotte da Qwen3.6: quelle bozze richiedono nuova estrazione.

Applicare `20261014160000_version_packshot_xy_coordinates.sql` prima di usare il nuovo
backend. Distribuire backend e lettore frontend insieme: `/interactive-offers` restituisce
sempre array X/Y, indipendentemente dalla versione conservata nel database.
I vecchi payload di estrazione con array senza versione restano leggibili come Y/X;
un array già normalizzato deve indicare versione 2 per evitare una seconda conversione.

## Granularità delle offerte estratte

Il prompt condiviso da Gemini e Ollama definisce l’offerta come gruppo promozionale, non come singola confezione visibile. Varianti di gusto, profumazione o tipologia della stessa marca e linea vengono accorpate solo con formato, prezzo, validità e condizioni identici e descrizione/prezzo comuni. Il formato resta quello della singola alternativa acquistabile; non si crea un multipack sommando le confezioni mostrate. `format.varianti` resta riservato alle differenze di formato. Il box deve comprendere il gruppo completo. Anche il prompt di recupero delle pagine sparse conserva questa regola. La regola orienta il modello, ma richiede verifica sulle nuove estrazioni; non modifica automaticamente le offerte esistenti.

L’accorpamento non cambia i multipack dichiarati nel testo: yogurt “125X2” resta `2x125 g` anche quando sono illustrati gusti alternativi. Le confezioni alternative non vengono sommate e il multipack reale non viene ridotto alla singola unità.

## Richieste Ollama multipagina

**Vincolo operativo per `gemma4:26b-mlx`: una pagina per chunk.** Il 15 ottobre 2026 la prova con due immagini per richiesta ha prodotto numerosi box sul prodotto sbagliato. La stessa configurazione a pagina singola ha migliorato localizzazione e dati nel campione ed è risultata più veloce. Il vincolo è documentato e non ancora imposto automaticamente dal codice. I limiti osservati e i parametri della configurazione verificata sono nel [resoconto delle prove locali](local-model-evaluation.md).

Il numero totale di pagine del PDF non cambia questo vincolo: 50 pagine con chunk da 1 comportano 50 richieste singole. La validità sintattica del box e di `source_page` non dimostra che il ritaglio corrisponda al prodotto. Verificare anche immagini effettive, completezza dei gruppi e condizioni commerciali.

`OLLAMA_CHUNK_SIZE_PAGES` accetta 1 (default) oppure 2. Con 2, il provider invia due immagini in ordine nella stessa richiesta `/api/chat`, non due chiamate separate. Lo schema richiede `source_page` relativo al chunk (1 o 2), poi il provider lo converte in numero assoluto del PDF. Indici mancanti, fuori intervallo o non interi fanno fallire il chunk; nessun ritaglio viene associato per ipotesi a una pagina. La pagina finale dispari usa una sola immagine. I checkpoint ripresi mantengono la dimensione salvata nel job originale. Per misurare la modalità a due pagine, avviare una nuova estrazione.
