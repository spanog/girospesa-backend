# API Reference

Il backend FastAPI è l'unica API applicativa. Le chiamate autenticate usano un JWT Supabase nell'header `Authorization: Bearer <token>`.

## Offerte

| Metodo | Path | Accesso | Descrizione |
| --- | --- | --- | --- |
| `GET` | `/offers` | Pubblico | Offerte confermate e attive, con ricerca, filtri e paginazione; non espone un parametro di ordinamento. Accetta `q`, `category`, `subcategory`, `supermarket_id` e `supermarket_ids`. |
| `POST` | `/guest-location` | Pubblico | Valida una posizione guest e imposta il cookie tecnico firmato usato dalla discovery. |
| `DELETE` | `/guest-location` | Pubblico | Rimuove il cookie tecnico di località guest. |

Un'offerta contiene i propri dati, validità, prezzo, formato strutturato e `image_url`. Non esistono endpoint per catalogo prodotti, dettagli prodotto o preferiti prodotto.

## Geocoding

| Metodo | Path | Accesso | Descrizione |
| --- | --- | --- | --- |
| `GET` | `/geocoding/addresses?query={query}` | Pubblico | Suggerimenti di indirizzi italiani per i form. |
| `GET` | `/geocoding/locations?query={query}` | Pubblico | Località selezionabili per la discovery guest. |
| `GET` | `/geocoding/locations/reverse?lat={lat}&lng={lng}` | Pubblico | Etichetta leggibile di coordinate geografiche. |

I client non contattano il provider di geocoding: il backend ne mantiene configurazione, credenziali e migrazioni.

## Volantini

| Metodo | Path | Accesso | Descrizione |
| --- | --- | --- | --- |
| `GET` | `/flyers/public` | Pubblico | Volantini pubblici correnti nel raggio attivo. Per i guest richiede il cookie di località firmato; senza località restituisce `428 guest_location_required`. |
| `GET` | `/supermarkets?with_active_offers=true` | Pubblico | Sedi nel raggio attivo, anche per admin e gestori. Per i guest richiede il cookie di località firmato; senza località restituisce `428 guest_location_required`. |
| `GET` | `/flyers/targets` | Admin/manager | Sedi selezionabili nella gestione volantini: tutte le sedi attive per admin, solo sedi assegnate per gestore. |
| `GET` | `/flyers` | Admin/manager | Elenco volantini in gestione. |
| `GET` | `/flyers/{flyer_id}` | Admin/manager | Dettaglio e stato di estrazione. |
| `GET` | `/flyers/{flyer_id}/file` | Pubblico se volantino pubblico e confermato; altrimenti admin/manager | Restituisce la rappresentazione binaria del volantino con disposition `inline`; il client non riceve URL Supabase. |
| `GET` | `/flyers/{flyer_id}/preview` | Stesso accesso del download | Restituisce la thumbnail WebP tramite backend; le preview pubbliche sono cacheabili, senza URL Supabase esposto. Per file storici la genera e persiste alla prima richiesta. |
| `GET` | `/flyers/{flyer_id}/preview-url` | Stesso accesso del download | Restituisce URL firmato breve della thumbnail WebP per workflow amministrativi privati. |
| `POST` | `/flyers/upload-url` | Admin/manager | Crea upload firmato per il bucket privato `flyers`. |
| `POST` | `/flyers/upload/complete` | Admin/manager | Valida il file e crea il volantino `pending`. |
| `POST` | `/flyers/{flyer_id}/extract` | Admin/manager | Avvia o riprende l'estrazione AI. |
| `GET` | `/flyers/{flyer_id}/draft-offers` | Admin/manager | Elenca le bozze offerta. |
| `PATCH` | `/flyers/{flyer_id}/draft-offers/{offer_id}` | Admin/manager | Modifica una bozza. |
| `POST` | `/flyers/{flyer_id}/draft-offers/{offer_id}/image` | Admin/manager | Carica o sostituisce l'immagine della bozza. |
| `POST` | `/flyers/{flyer_id}/offers/confirm` | Admin/manager | Conferma e pubblica le offerte del volantino. |

L'estrazione salva subito le bozze di ogni chunk riuscito. In caso di errore, `extraction_metadata` indica il checkpoint riprendibile; una nuova richiesta `extract` continua dal chunk successivo senza duplicare le offerte. Il crop estratto, quando disponibile, viene salvato in `offers.image_url`.

## Liste

| Metodo | Path | Accesso | Descrizione |
| --- | --- | --- | --- |
| `GET` | `/lists`, `/lists/active` | Autenticato | Elenca o restituisce la lista attiva. |
| `POST` | `/lists/{id}/items` | Membro | Aggiunge una voce manuale o un'offerta. |
| `PATCH` | `/lists/{id}/items/{item_id}` | Membro | Aggiorna una voce. |
| `DELETE` | `/lists/{id}/items/{item_id}` | Membro | Elimina una voce. |
| `GET` | `/lists/{id}/deal-freshness` | Membro | Verifica validità delle offerte pinnate. |
| `POST` | `/lists/{id}/clear-stale-offers` | Membro | Converte offerte non disponibili in voci manuali. |
| `POST` | `/lists/{id}/invite` | Proprietario | Crea un invito alla lista. |
| `GET` | `/lists/{id}/members` | Membro | Elenca i membri. |

## Notifiche

| Metodo | Path | Accesso | Descrizione |
| --- | --- | --- | --- |
| `POST` | `/push/subscribe` | Autenticato | Registra Web Push. |
| `POST` | `/push/native/subscribe` | Autenticato | Registra token FCM. |
| `POST` | `/ops/cron/notifications` | Ops secret | Drena i job di notifica. |

La conferma di un volantino accoda un job idempotente `flyer_published` e risponde senza attendere consegne. Se `valid_from` è futura, il job viene eseguito alle 10:00 `Europe/Rome` di quel giorno; senza data, viene eseguito subito. Il worker ricontrolla che il volantino sia ancora pubblico e valido, poi materializza job figli per tutti gli admin, per il manager della sede pubblicata e per i customer nel raggio della loro posizione di ricerca o casa, crea lo storico in `app_notifications`, invia Web Push/FCM solo con notifiche account abilitate e collega il tap a `/volantini?supermarket_id=<UUID-sede>`.

## Altri endpoint

- `/users`: profilo, geocoding e avatar.
- `/supermarkets`: elenco e filtri di distanza.
- `/purchases`: storico acquisti.
- `/analytics/b2b`: analytics con API key.
- `/ops/cron/daily-maintenance`: manutenzione giornaliera.
