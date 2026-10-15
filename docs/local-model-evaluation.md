# Valutazione locale dei modelli per volantini

## Configurazione utilizzabile: Gemma 4 26B-A4B MLX

Le prove del 15 ottobre 2026 sul Mac M1 Max con 64 GB di memoria indicano `gemma4:26b-mlx` come opzione utilizzabile in locale, mantenendo la revisione delle bozze e il vincolo operativo di **una pagina per chunk e per richiesta**.

```dotenv
LLM_PROVIDER=ollama
OLLAMA_MODEL=gemma4:26b-mlx
OLLAMA_CHUNK_SIZE_PAGES=1
OLLAMA_IMAGE_DPI=150
OLLAMA_CONTEXT_TOKENS=32768
OLLAMA_MAX_OUTPUT_TOKENS=16384
OLLAMA_TIMEOUT_SECONDS=1200
```

La CLI deve usare `--model gemma4:26b-mlx --chunk-pages 1`. Dopo una modifica del `.env`, riavviare FastAPI. Avviare una nuova estrazione per cambiare la dimensione del chunk: una ripresa conserva quella del checkpoint originale. Il vincolo è operativo e documentato; il codice accetta ancora due pagine e non applica un blocco specifico per modello. Questa configurazione locale non modifica il provider Gemini di produzione o la skill giornaliera.

## Cosa è successo

È stato usato lo stesso PDF DIPER di 16 pagine, con validità stampata dal 14 al 27 ottobre 2026 e SHA-256 `7308957ee1f2f66497f2475ea1b2424143d673da69f52a51788834336f40e57b`. Il prompt corrente richiede coordinate XY nominate e accorpamento delle alternative della stessa offerta. Le due prove del 26B hanno mantenuto modello, prompt e budget configurati, cambiando le pagine per richiesta. Il carico esterno della macchina non è stato isolato.

| Prova | Durata di estrazione | Esito osservato |
|---|---:|---|
| Gemma 31B MLX, 2 pagine | 1 h 27 min 15,7 s | 205 offerte; impostazioni di confronto documentate separatamente |
| Gemma 26B MLX, 2 pagine | 32 min 45,6 s | 206 offerte; un retry per output troncato; gravi errori di localizzazione |
| Gemma 26B MLX, 1 pagina | 26 min 38,5 s | 205 offerte; 16/16 chunk conclusi; nessun retry registrato |

Nel batch doppio, almeno 16 dei 27 ritagli verificati sulle pagine 2 e 4 non includevano il proprio prodotto: Asiago puntava alle olive, Pecorino Romano al Maasdammer, Certosino e Galbanino al dessert Stuffer. I marcatori sovrapposti erano quindi associati a offerte diverse con box errati, non una prova di record duplicati dello stesso prodotto.

Nella prova a pagina singola, tutti i 27 ritagli delle stesse pagine includevano almeno parte del prodotto corretto; gli scambi osservati erano scomparsi. Il tempo è diminuito di circa 6 minuti e 7 secondi, pari al 18,7%. Questo dimostra un miglioramento in quelle esecuzioni, non la causa interna del modello né una garanzia universale sulla localizzazione.

Un volantino di 50 pagine con chunk da 1 continua a generare 50 richieste singole. Il rischio osservato riguarda più immagini nella stessa richiesta, non il semplice aumento delle pagine totali. Un nuovo esperimento multipagina deve verificare visivamente offerte, pagine e ritagli prima di autorizzare un diverso assetto operativo.

## Accuratezza e limiti residui

Il campione a pagina singola comprende 62 offerte sulle pagine 1, 2, 3, 4, 8 e 16: tutte presenti, 62 valori numerici dei prezzi coincidenti e 60 formati/basi prezzo coerenti. Non è una misura globale delle 205 offerte. Restano errori:

- Grana Padano: il nome riporta 80 g, ma il formato strutturato non conserva la quantità.
- Parmacotto: prezzo di 15,90 euro/kg associato a confezione 250 g; i 250 g sono la soglia per ricevere un omaggio.
- Sottilette: marca trascritta come `Classi`.
- Alcuni ritagli tagliano confezioni o non comprendono tutte le alternative del gruppo.

Philadelphia 6x25 g, Granpiada 330 g e Peroni 3x33 cl risultano corretti nella prova singola. Tarallucci è conservato come omaggio nelle note dei biscotti, senza una seconda offerta a pagamento. La validità persistita 15–16 ottobre non coincideva con il PDF: il backend privilegia le date del volantino caricato e questo errore applicativo non può essere attribuito al modello dai soli dati salvati. Impostare le date reali del documento durante il caricamento.

## Prompt coordinate e offerte con più confezioni

Mantenere il contratto corrente: `packshot_bbox` con `x_min`, `y_min`, `x_max`, `y_max`, normalizzazione interna XY e `packshot_bbox_version=2`. La modifica introdotta durante le prove Qwen esplicita gli assi e riduce l'ambiguità del formato; non dimostra da sola una maggiore accuratezza geometrica. Le vecchie risposte usavano array YX: un rollback del solo prompt sarebbe incoerente con schema e parser attuali. Non reinterpretare i box storici senza la loro versione.

Mantenere anche la regola condivisa di accorpamento: una sola offerta per alternative della stessa marca e linea con formato, prezzo, validità e condizioni uguali; il box richiesto comprende tutte le confezioni alternative. Alternative di gusto non diventano un multipack, mentre un multipack realmente dichiarato conserva quantità e peso. Prezzi, formati o condizioni diversi richiedono offerte distinte; un omaggio non è una seconda offerta indipendente a pagamento.

Gemma 26B a pagina singola ha rispettato Accadì 1 L, yogurt 2x125 g e tè 1,5 L nel campione, ma non sempre ha ritagliato l'intero gruppo. Non è stato svolto un confronto controllato tra prompt precedente e corrente sullo stesso modello con impostazioni e carico identici: non è possibile attribuire al prompt tutto il miglioramento o concludere che ripristinare il precedente sarebbe meglio. La scelta operativa è conservare prompt e schema correnti, intervenendo in modo mirato dopo verifica di esempi reali.

## Evidenze locali

Sorgenti delle prove: batch doppio `fe513d1a-e85a-4e97-8f44-284886eac238`; pagina singola `c077c97b-0f18-4a3d-8447-013496b7b745`. Le durate derivano da `extraction_metadata`, non dalla creazione delle copie pubblicate.

Snapshot e fogli diagnostici sono artefatti locali del workspace, non dipendenze necessarie per eseguire l'app:

- `output/benchmarks/gemma26b-mlx-2026-10-15/`: report, snapshot e ritagli del batch doppio.
- `output/benchmarks/gemma26b-mlx-single-2026-10-15/`: report, snapshot e ritagli della pagina singola.

I percorsi sono relativi alla root del workspace, esterna alla directory del repository backend. Non contengono credenziali. Per confronti futuri usare il PDF originale e dati attesi verificati, non l'output di un altro modello come verità di riferimento.
