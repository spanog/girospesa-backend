# Skill Codex per volantini

La gestione giornaliera dei volantini è una skill Codex avviata manualmente con `$girospesa-volantini`. Non esiste alcun job backend, cron o automazione che scarichi, carichi o pubblichi volantini senza un'azione esplicita dell'operatore.

## Configurazione versionata

Le fonti stanno accanto alla skill in `../.agents/skills/girospesa-volantini/flyer-sources.yaml`. Il backend non contiene URL di volantini o UUID delle filiali: espone soltanto il client e il preflight. Aggiungere le fonti solo dopo avere recuperato gli UUID reali delle filiali da GiroSpesa.

```yaml
sources:
  - id: eurospin-cittanova
    listing_url: https://www.eurospin.it/volantino-store-eurospin/?codice_pv=709501
    target_supermarket_ids:
      - <uuid-filiale>
    enabled: true
```

`id` è stabile e univoco, `listing_url` deve usare HTTPS, `target_supermarket_ids` contiene uno o più UUID di `supermarkets.id` e `enabled` esclude temporaneamente una fonte. Validare ogni modifica prima dell'uso:

```bash
.venv/bin/python -m scripts.flyer_ingestion.cli validate-config --config ../.agents/skills/girospesa-volantini/flyer-sources.yaml
```

## Prima esecuzione e duplicati

Lo stato locale della skill, `~/.codex/state/girospesa-volantini.json`, conserva impronta, eventuale ID volantino e risultato delle esecuzioni. Non viene però usato per decidere se caricare un file: anche se non esiste ancora, il primo run invia ogni PDF candidato a `POST /flyers/ingestion-preflight` prima di creare un URL firmato o un oggetto Storage.

Il preflight è read-only. Confronta SHA-256 con i volantini sorgente nelle filiali target; se il byte stream è diverso, confronta data di validità, numero di pagine e impronta visiva di tutte le pagine dei PDF salvati. Gli esiti sono:

| Esito | Azione |
| --- | --- |
| `known` | Tutte le filiali hanno già il documento; nessun upload. |
| `partial` | Caricare solo `upload_supermarket_ids`. |
| `new` | Caricare per tutte le filiali target. |
| `indeterminate` | Non caricare; richiedere intervento in chat. |

Di conseguenza, se gli ultimi volantini sono stati caricati ieri, la prima esecuzione di oggi li trova `known` anche senza stato locale e non li duplica.

## Identità tecnica e client diretto

La skill non dipende dalla UI amministrativa né dalla sessione del browser. Un account tecnico `admin`, diverso dagli account personali, riceve una password casuale conservata soltanto nel Portachiavi macOS e ottiene un bearer JWT breve da Supabase a ogni esecuzione. Il client usa gli endpoint privati già esistenti nello stesso ordine della UI: preflight, URL firmato, upload diretto in Storage, completamento, estrazione, polling e conferma. Non usa il service-role key per le azioni giornaliere.

Il provisioning è una sola operazione locale: `.venv/bin/python -m scripts.flyer_ingestion.cli provision-agent --email volantini-bot@local.test`. In un ambiente non locale il runtime della skill deve esporre `GIROSPESA_FLYER_AGENT_PUBLISHABLE_KEY`; la password non va mai inserita in YAML, repository o chat.

## Acquisizione ed estrazione

La skill cerca tutti i volantini validi oggi o futuri. Preferisce il PDF originale. Quando il viewer mostra `download`, lo usa e controlla i PDF recenti in Download: un browser può salvare il file senza restituirne il percorso all'agente, e questo non deve essere interpretato come assenza del PDF. Il candidato viene spostato subito in `.agents/skills/girospesa-volantini/downloads/`, quindi validato con `validate-pdf`; soltanto se non esiste o è invalido la skill acquisisce le immagini nell'ordine corretto e genera un PDF con `build-pdf` nella stessa cartella. Dopo `known` o dopo `done` con offerte confermate, la skill elimina il solo PDF della sessione; lo conserva per la diagnosi in ogni esito di stop. Se una data non è disponibile sul sito, chiede le due date in chat prima del preflight.

Dopo un upload `new` o `partial`, il client della skill avvia l'estrazione e controlla lo stato. Per un errore Gemini con checkpoint riprendibile, sono ammessi al massimo tre riavvii, dopo 2, 10 e 30 secondi. La pubblicazione automatica avviene solo con stato `done` e almeno una bozza; CAPTCHA, login, PDF non valido, zero bozze o risultato `indeterminate` interrompono il flusso senza pubblicazione. Il client elimina il PDF temporaneo soltanto dopo `known` o `done`; negli altri casi lo conserva per diagnosi.
