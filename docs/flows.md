# Main Flows

## Flows

### 1. Lista spesa con offerte pinnate

```
Frontend
  Utente aggiunge offerta attiva alla lista
                    │
                    ▼
  Lista salva `pinned_offer_id` e snapshot essenziale
                    │
                    ▼
  GET lista proietta offerte scadute o eliminate come voce manuale
                    │
                    ▼
  UI raggruppa per supermercato le offerte attive e mostra le altre in “Senza offerta”
```

### 2. Upload volantino e estrazione AI

```
  Utente sceglie PDF → POST /flyers/upload-url
                         │
                         ▼
  Frontend: upload diretto a Supabase Storage (bucket flyers privato)
                         │
                         ▼
  Frontend: POST /flyers/upload/complete
                         │
                         ▼
  Backend: scarica oggetto Storage, valida tipo + dimensione
  Backend: calcola SHA-256 server-side → controlla duplicati (409 se già esiste)
  Backend: crea riga flyers con status='pending'
                         ▼
  Admin / manager: POST /flyers/{id}/extract
                         │
                         ▼
  ExtractionService:
    → scarica file
    → Gemini estrae offerte
    → normalizza offerte e salva crop in `offers.image_url`
    → inserisce draft offers con is_confirmed=false
    → aggiorna status → 'done'
                         │
                         ▼
  Admin / manager:
    GET /flyers/{id}/draft-offers
    → response ricostruisce format_label da format se manca
    PATCH draft offers
    POST /flyers/{id}/offers/confirm (pubblica solo offerte)
                         │
                         ▼
  Frontend pubblico: GET /offers / GET /flyers/public
```

### 3. Push notification su nuova offerta

```
  Browser utente: richiede permesso notifiche
  Browser: genera subscription {endpoint, p256dh, auth_key}
  Frontend: POST /push/subscribe
  Backend: salva in push_subscriptions (upsert per user_id + endpoint)
                         │
  Admin o manager conferma nuove offerte
  Backend pubblica le offerte confermate
                         │
                         ▼
  Backend accoda notification_jobs idempotenti
                         │
                         ▼
  Worker notifiche trova tutti gli admin, manager della sede e customer nel raggio del supermercato
    Per ogni destinatario, in job figlio parallelo:
      → insert app_notifications.flyer_published
      → se notifications_enabled=true, fetch push_subscriptions
      → per ogni subscription registrata:
            send_push_notification (VAPID, pywebpush)
            se 410 Gone → cancella subscription stale
```

### 4. Lista condivisa

```
  Proprietario: POST /lists/{id}/invites
    Backend: crea invito email diretto su list_invites
                    │
  Destinatario: apre /lista?panel=inviti
  Destinatario: POST /lists/invites/{invite_id}/accept
    Backend: inserisce list_members {role: 'member'}
    Backend: segna invite → accepted
    Backend: invia al proprietario notifica di accettazione con nome membro
                    │
  Ora entrambi vedono la lista in tempo reale
  (SSE backend su /lists/{id}/events, alimentato da Postgres NOTIFY)
```

### 5. Freshness delle offerte in lista

```
  Utente aggiunge item con pinned_offer_id dalla griglia offerte
  Volantino scade o prezzo cambia
                    │
  Frontend: GET /lists/{id}/deal-freshness
    Backend: per ogni item con pinned_offer_id:
      ┌──────────────────────────────────────────────┐
      │  offer non trovata       → unavailable        │
      │  is_active = false       → expired            │
      │  prezzo cambiato > 0.01€ → price_changed      │
      │  tutto ok                → fresh              │
      └──────────────────────────────────────────────┘
    Ritorna stato + prezzo attuale per ogni item
  Frontend: mostra badge/warning su prodotti scaduti o variati
```

---
## Ritagli offerta nelle bozze

Durante l'estrazione PDF, Gemini può restituire pagina sorgente e bounding box normalizzato del packshot. Il backend rende la pagina, amplia il box con un margine adattivo anti-taglio e salva il crop in `product-images`, assegnandone l'URL a `offers.image_url`. La review delle bozze lo mostra automaticamente. Coordinate mancanti o non valide non bloccano l'estrazione e lasciano la bozza senza immagine; immagini manuali esistenti non vengono sovrascritte.
