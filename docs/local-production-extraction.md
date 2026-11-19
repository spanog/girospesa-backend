# Estrazione locale con dati in produzione

Il backend accetta risultati di inferenza da un worker sul Mac. PDF, bozze, immagini, avanzamento e pubblicazione restano nel database e nello Storage di produzione. Il modello gira su Ollama via loopback; non occorre esporre il Mac su Internet. L'estrazione Gemini delle normali richieste rimane disponibile per altri volantini.

## Componenti e distribuzione

Le API e i test backend sono versionati. La directory `local_extraction/`, nella root backend, è esclusa integralmente da Git: contiene script della macchina, configurazione, test del worker, copie PDF e checkpoint. Non viene distribuita su Render e deve essere preservata o ricreata separatamente quando si cambia Mac. Le credenziali restano nel file protetto della skill `../.agents/skills/girospesa-volantini/runtime-config.local.json`; non sono copiate negli script o nei checkpoint.

Prima di usare il worker, distribuire questa versione del backend. Il server richiede `DB_DSN` o `DATABASE_URL` già previsto dal deploy, con permessi di lettura/scrittura su flyer e offerte. Non occorrono nuove tabelle o migrazioni: sessione e ricevute dei chunk sono conservate in `flyers.extraction_metadata`, con `execution_mode: local`.

## Contratto API

Tutti gli endpoint richiedono un bearer Supabase di un account il cui profilo server-side abbia ruolo `admin`. Nessun ruolo dichiarato dal client conferisce accesso.

| Metodo | Percorso sotto `/flyers` | Operazione |
| --- | --- | --- |
| GET | `/local-extraction/capabilities` | Verifica versione protocollo e configurazione server. |
| POST | `/{id}/local-extraction/claim` | Acquisisce o riprende la sessione per `worker_id`. |
| POST | `/{id}/local-extraction/heartbeat` | Rinnova la presa in carico per 600 secondi. |
| PUT | `/{id}/local-extraction/chunks/{index}` | Salva candidati e checkpoint nella stessa transazione. |
| POST | `/{id}/local-extraction/finish` | Completa immagini mancanti e conclude l'estrazione. |
| POST | `/{id}/local-extraction/fail` | Segnala errore, conserva checkpoint e rilascia la presa in carico. |

Il claim include modello, hash SHA-256 PDF, pagine, dimensione chunk, DPI, contesto, budget output e hash prompt. Il backend verifica impronta del file e numero delle pagine dell'originale. La ripresa richiede la stessa configurazione. Non è consentito combinare checkpoint o bozze cloud con una nuova sessione locale.

Ogni chunk contiene `worker_id` e `products`; ogni candidato indica `source_page` assoluta e, quando presente, `packshot_bbox` con coordinate XY nominate sulla griglia 0–1000. Il server applica gli stessi normalizzatori, regole dei formati e chiavi offerte della pipeline ordinaria. Il client negozia `client_packshots` tramite capabilities. Con supporto disponibile, il claim include `packshot_mode: client` e ogni chunk aggiunge `packshots`, con pagina, coordinate nominate e WebP in base64. Il Mac riutilizza il PDF già acquisito e le stesse funzioni di rendering del server; il backend continua a normalizzare offerte, espandere varianti, validare pagine e coordinate, controllare formato e dimensioni delle immagini e caricarle nello Storage con credenziali esclusivamente server-side. Le varianti con identica pagina e box condividono il ritaglio. Non sono accettati URL immagine arbitrari. Il limite è 500 candidati e 1 MB di prodotti, più 8 MB di ritagli codificati, massimo 512 KiB e 640 pixel per WebP statico.

In assenza della capability nuova, il client mantiene i ritagli server-side: la distribuzione del backend può precedere o seguire l'aggiornamento degli script senza interrompere i nuovi job. La versione di protocollo resta 1; claim privi di `packshot_mode` conservano il contratto precedente.

Gli aggiornamenti bloccano la riga flyer in PostgreSQL. Un invio identico è idempotente; stesso indice con contenuto diverso, chunk fuori ordine, pagina fuori chunk o worker con lease scaduta restituiscono `409`. Inserimento offerte e aggiornamento checkpoint fanno commit insieme. In modalità client, le immagini sono caricate prima del commit delle righe e il checkpoint comprende anche il loro contenuto: un replay riuscito non ripete upload o inserimenti. I percorsi includono flyer e SHA-256 dei byte; un retry dopo rollback riusa lo stesso oggetto, senza creare nuove copie. Un errore dopo l'upload ma prima del commit può lasciare temporaneamente un oggetto non referenziato; la transazione non avanza e il retry riusa il percorso. Nessun nuovo download del PDF o query PostgREST di ricerca/aggiornamento ritagli è necessario per i chunk client. Nel percorso server precedente, i ritagli sono completati dopo il commit e la ripetizione riprova quelli mancanti. `finish` richiede tutti i chunk e almeno una bozza valida; non pubblica. La conferma usa il percorso esistente `/{id}/offers/confirm`.

L'avvio cloud usa un aggiornamento condizionale su stato e `updated_at` e rifiuta flyer assegnati al percorso locale. Il recovery all'avvio del server lascia intatte queste sessioni: il worker non vive nel processo web.

## Configurazione e uso sul Mac

`local_extraction/config.json` è indipendente da `.env`. Configurazione operativa iniziale: `gemma4:26b-mlx`, una pagina per richiesta, 150 DPI, contesto 32768, output 16384 token, timeout inferenza 1200 secondi. Il worker impone una pagina per questo modello, secondo le prove documentate in [local-model-evaluation.md](local-model-evaluation.md).

Da root backend:

```bash
.venv/bin/python local_extraction/run.py check
.venv/bin/python local_extraction/run.py run <flyer-id-produzione>
.venv/bin/python local_extraction/run.py status <flyer-id-produzione>
.venv/bin/python local_extraction/run.py confirm <flyer-id-produzione>
```

`run` riprende lo stesso flyer; non carica un nuovo PDF e non conferma automaticamente. `status` legge solo checkpoint locali. `confirm` è una pubblicazione esplicita e verifica successivamente presenza di offerte confermate e destinazioni pubblicate. Un risultato del modello non garantisce accuratezza: resta necessaria la verifica editoriale prevista dal proprio flusso.

La skill usa gli stessi script tramite il client locale quando `local_extraction.enabled` nel suo runtime JSON vale `true`. `verify_production.py` verifica anche disponibilità del protocollo prima dell'acquisizione. Mancanza delle API blocca il run: non attiva Gemini come fallback. Ricerca fonti, preflight, upload, serializzazione dei worker, inventario, retry, monitor e conferma restano gestiti dalla skill. I token sono rinnovati in memoria durante elaborazioni lunghe. Non modificare `.env` o provider globale di produzione per attivare questa modalità.

## Traffico e limiti della riduzione

Dopo `upload/complete`, il worker conserva atomicamente una copia del PDF acquisito, verificandone SHA-256 contro quello calcolato dal server. Il flusso ordinario della skill non richiede quindi un URL firmato né riscarica la sorgente da Supabase. Un recupero manuale per flyer ID, privo di copia locale, può ancora effettuare un download iniziale: conservare gli artefatti del job fino alla conferma. Il server legge ancora l'oggetto per validare upload, impronta e anteprima, e verifica il numero di pagine al claim usando la cache locale esistente. Le anteprime restano generate dal server; questa modifica non elimina ogni trasferimento Storage.

Le API locali riutilizzano il pool PostgreSQL esistente, con commit/rollback espliciti, selezionano soltanto i campi necessari del flyer e inseriscono le offerte di un chunk in batch. Non occorrono Redis, nuovi servizi o migrazioni. La riduzione degli heartbeat da 45 a 180 secondi diminuisce del 75% i rinnovi periodici durante inferenza, mantenendo lease di 600 secondi e fencing. Non equivale al 75% di risparmio dell'egress totale: upload, consultazioni, altre query, cache e numero di utilizzatori restano determinanti. Nessuna garanzia di permanenza nelle quote Free senza misure del ciclo completo.

## Interruzioni e ripresa

Il worker rinnova la lease ogni 180 secondi durante inferenza e invio. Salva risposta completa e ritagli prima di inviarli: con errore di rete conserva il JSON e riprende dall'ultimo chunk confermato. Tre tentativi di consegna usano attese di 2 e 10 secondi. Il provider mantiene i propri retry per risposta LLM invalida. I retry della skill passano sempre dal worker locale.

Con interruzione della skill, usare il suo `background-resume --job-id`, senza secondo upload. Un altro worker può riprendere una sessione dopo 600 secondi senza heartbeat; i risultati del vecchio proprietario vengono rifiutati. Il Mac deve essere acceso, con Ollama disponibile e connessione di rete. Dopo sospensione o perdita di rete, lo stato può restare `processing` fino alla ripresa esplicita; la lease scaduta è conservata nei metadati, non avvia automaticamente un processo server.

Conservare PDF e checkpoint degli errori. Dopo conferma verificata, il client elimina la copia PDF del worker; l'audit locale resta. La skill gestisce separatamente il PDF di acquisizione. Gli script locali non ricevono chiavi amministrative Supabase e non accedono direttamente al database.

## Verifica

```bash
.venv/bin/python -m pytest tests -q --ignore=tests/integration --ignore=tests/performance
.venv/bin/python -m pytest tests/integration -q
.venv/bin/python -m pytest local_extraction/tests -q
.venv/bin/python -m pytest ../.agents/skills/girospesa-volantini/runtime/tests -q
```

I test automatici usano inferenza e Storage simulati e PostgreSQL sullo stack isolato `girospesa-itest`; non inviano offerte in produzione e non scaricano modelli.

## Verifica ottimizzazione egress — 19 novembre 2026

Checkout verificato con 668 test unitari backend, 69 test di integrazione sullo stack Docker isolato, 24 test del worker locale e 85 test della skill. Il campione automatico confronta anche i byte del ritaglio locale con quelli del renderer server condiviso. Nessun volantino caricato o pubblicato in produzione per questi test. Il proprietario conferma assenza di job già avviati.

Le modifiche server descritte qui richiedono un deploy verificato; commit e push non ne dimostrano l'attivazione in produzione. Il riuso della sorgente acquisita e il nuovo intervallo di heartbeat sono disponibili ai prossimi processi locali. I ritagli client sono negoziati tramite capability: finché il backend distribuito non li supporta, rimane operativo il percorso server precedente.
