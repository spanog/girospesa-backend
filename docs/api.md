# API Reference

Il backend FastAPI è l'unica API applicativa. Le chiamate autenticate usano un JWT Supabase nell'header `Authorization: Bearer <token>`.

## Offerte

| Metodo | Path | Accesso | Descrizione |
| --- | --- | --- | --- |
| `GET` | `/offers` | Pubblico | Offerte confermate e attive, con ricerca, filtri e paginazione. |

Un'offerta contiene i propri dati, validità, prezzo, formato strutturato e `image_url`. Non esistono endpoint per catalogo prodotti, dettagli prodotto o preferiti prodotto.

## Volantini

| Metodo | Path | Accesso | Descrizione |
| --- | --- | --- | --- |
| `GET` | `/flyers/public` | Pubblico | Volantini pubblici con offerte attive. |
| `GET` | `/flyers` | Admin/manager | Elenco volantini in gestione. |
| `GET` | `/flyers/{flyer_id}` | Admin/manager | Dettaglio e stato di estrazione. |
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

La conferma di un volantino accoda job idempotenti `flyer_published`. Il worker crea lo storico in `app_notifications`, invia Web Push/FCM e collega il tap a `/offerte` con il supermercato nel query string.

## Altri endpoint

- `/users`: profilo, geocoding e avatar.
- `/supermarkets`: elenco e filtri di distanza.
- `/purchases`: storico acquisti.
- `/analytics/b2b`: analytics con API key.
- `/ops/cron/daily-maintenance`: manutenzione giornaliera.
