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

Ogni chunk contiene `worker_id` e `products`; ogni candidato indica `source_page` assoluta e, quando presente, `packshot_bbox` con coordinate XY nominate sulla griglia 0–1000. Il server applica gli stessi normalizzatori, regole dei formati e chiavi offerte della pipeline ordinaria. Il backend genera i ritagli dal PDF originale e li salva nello Storage di produzione. I payload sono limitati a 500 candidati e 1 MB per chunk.

Gli aggiornamenti bloccano la riga flyer in PostgreSQL. Un invio identico è idempotente; stesso indice con contenuto diverso, chunk fuori ordine, pagina fuori chunk o worker con lease scaduta restituiscono `409`. Inserimento offerte e aggiornamento checkpoint fanno commit insieme. I ritagli sono completati dopo il commit; una ripetizione riprova soltanto quelli mancanti. `finish` richiede tutti i chunk e almeno una bozza valida; non pubblica. La conferma usa il percorso esistente `/{id}/offers/confirm`.

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

## Interruzioni e ripresa

Il worker rinnova la lease ogni 45 secondi durante inferenza e invio. Salva la risposta completa prima di inviarla: con errore di rete conserva il JSON e riprende dall'ultimo chunk confermato. Tre tentativi di consegna usano attese di 2 e 10 secondi. Il provider mantiene i propri retry per risposta LLM invalida. I retry della skill passano sempre dal worker locale.

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
