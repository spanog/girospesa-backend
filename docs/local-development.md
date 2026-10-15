# Local Development

## Workflow

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
- Data API: nuove tabelle, sequenze e funzioni nello schema `public` non ricevono grant impliciti. La migration che le crea deve assegnare esplicitamente i soli privilegi necessari; le policy RLS non sostituiscono i grant.
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
  - profilo admin con Comune ISTAT Polistena (`080061`), senza indirizzo o coordinate utente
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
| Servizio esterno | SMTP server | No | Necessario solo per invio mail da `/contact-requests` |

---

## Estrazione locale con Ollama

FastAPI può usare Ollama sul Mac anche per l'estrazione avviata dall'interfaccia web.
Avviare Ollama e configurare il `.env` locale:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:26b-mlx
OLLAMA_CHUNK_SIZE_PAGES=1
OLLAMA_TIMEOUT_SECONDS=1200
OLLAMA_CONTEXT_TOKENS=32768
OLLAMA_MAX_OUTPUT_TOKENS=16384
OLLAMA_IMAGE_DPI=150
```

Riavviare FastAPI dopo aver modificato il `.env`:

```bash
.venv/bin/python -m uvicorn main:app --reload --port 8000
```

Il normale caricamento e l'avvio dell'estrazione dalla webapp usano il provider selezionato.
Con Ollama non serve `GOOGLE_API_KEY`. Il modello deve essere già installato e supportare immagini.
Con `gemma4:26b-mlx`, mantenere **una pagina per chunk e per richiesta**. La prova a due pagine ha associato numerose offerte a coordinate di altri prodotti; la successiva prova a pagina singola ha eliminato questi scambi nel campione verificato. Il modello è utilizzabile in locale con revisione delle bozze, ma non garantisce formati o ritagli sempre corretti. Un PDF più lungo resta compatibile: aumentano le richieste, non le pagine di ciascuna richiesta. Vedere [risultati e limiti delle prove](local-model-evaluation.md).
Per cambiare modello, impostare `OLLAMA_MODEL=qwen3.8:27b-nvfp4` oppure
`OLLAMA_MODEL=qwen3.6:35b-a3b-nvfp4` e riavviare il backend.
Il provider non esegue fallback verso Google e non scarica modelli automaticamente.

### Elaborare un file senza database

Dalla directory del backend, questo comando produce un JSON locale senza scrivere su Supabase:

```bash
.venv/bin/python -m scripts.extraction.submit_flyer \
  tests/extraction_eval/fixtures/volantino-conad-ridotto.pdf \
  --provider ollama --model gemma4:26b-mlx --chunk-pages 1 \
  --supermarket Conad --output /tmp/conad-gemma4.json
```

Ripetere con gli altri modelli cambiando `--model` e `--output`.
Il comando legge il `.env` del backend anche se viene avviato da un'altra directory.
Le opzioni `--ollama-url`, `--timeout`, `--dpi`, `--context` e `--max-output` sovrascrivono
la configurazione per la sola esecuzione CLI.

I PDF sono rasterizzati a 150 DPI e analizzati una pagina alla volta. La numerazione
`source_page` viene assegnata dal provider alla pagina originale, indipendentemente dal modello.
Il workflow web salva risultati e checkpoint per chunk; in caso di errore può riprendere
l'estrazione dal chunk fallito. La CLI termina con errore e non scrive un risultato di successo
se un chunk fallisce. Un JSON malformato o un output troncato non viene trattato come estrazione vuota valida.

Se Ollama non risponde, verificarne l'avvio e `ollama list`. Per un modello mancante eseguire
`ollama pull <tag>`. In caso di timeout aumentare `OLLAMA_TIMEOUT_SECONDS`; per output troncati,
aumentare `OLLAMA_MAX_OUTPUT_TOKENS` mantenendo spazio sufficiente nel contesto anche per prompt e immagini.

### Aggiornamento del contratto coordinate

La migrazione `20261014160000_version_packshot_xy_coordinates.sql` è necessaria per le nuove estrazioni. Aggiunge una versione alle coordinate e aggiorna la materializzazione delle offerte pubblicate. Non modifica coordinate o immagini storiche. Dopo aggiornamento schema e codice, riavviare backend e aggiornare la pagina web prima di avviare una nuova estrazione. Il modello selezionato e gli altri parametri restano invariati.

## Prova sperimentale Gemma 31B MLX con due pagine per richiesta

Questa configurazione documenta la prova del 31B; non è una raccomandazione per il 26B. Con `gemma4:26b-mlx` mantenere `OLLAMA_CHUNK_SIZE_PAGES=1` e `--chunk-pages 1`. Non aumentare il chunk per accelerare o perché il PDF contiene più pagine. Un eventuale nuovo confronto multipagina richiede una verifica esplicita delle associazioni offerta/pagina/ritaglio prima dell'uso ordinario.

Configurazione locale per il confronto:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_MODEL=gemma4:31b-mlx
OLLAMA_CHUNK_SIZE_PAGES=2
OLLAMA_CONTEXT_TOKENS=32768
OLLAMA_MAX_OUTPUT_TOKENS=16384
OLLAMA_TIMEOUT_SECONDS=1200
OLLAMA_IMAGE_DPI=150
```

Il prompt corrente, la temperatura 0,1 e la quantizzazione restano invariati.
Contesto, limite output e timeout sono aumentati rispetto alla prova a pagina singola
per contenere due pagine e tutte le offerte senza troncamenti. Registrare questa
configurazione nel confronto: eventuali differenze non vanno attribuite soltanto al chunk.
Per la CLI, usare `--chunk-pages 2` insieme al modello Gemma; i budget vengono letti dal `.env`
o sovrascritti con `--context`, `--max-output` e `--timeout`.
La modalità normale si ripristina con `OLLAMA_CHUNK_SIZE_PAGES=1` e riavvio del backend.
