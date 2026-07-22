# Main Flows

## Flows

### 1. Ottimizzazione lista spesa

```
Frontend
  POST /optimize {list_id}
                    │
                    ▼
  Carica items non spuntati dalla lista
  Carica posizione utente (search_location, oppure home_location)
  Carica tutte le offerte attive nella finestra corrente (`valid_from <= oggi <= valid_to`, null-safe) con prodotto + supermercato
                    │
                    ▼ per ogni item
  Usa `pinned_offer_id` come default se ancora valido/vicino
  Usa `pinned_product_id` come match canonico se non c'è offerta specifica
  Gli item manuali restano nel gruppo `Senza offerta`
  L'MVP non espone UI di alternative o sostituzioni suggerite
  Filtra per distanza con PostGIS (`nearby_supermarkets`, `ST_DWithin`)
                    │
                    ▼
  Raggruppa item con offerta per supermercato
  Raggruppa item manuali senza offerta separatamente
                    │
                    ▼
  Risposta: store_groups[{supermercato, prodotti, subtotal,
            savings, distanza_km, alternative per item}]
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
    → Gemini estrae prodotti
    → normalizza prodotti
    → match prodotti esistenti + upsert draft offers con is_confirmed=false
    → aggiorna status → 'done'
                         │
                         ▼
  Admin / manager:
    GET /flyers/{id}/draft-offers
    → response ricostruisce format_label da format se manca
    PATCH draft offers
    POST /flyers/{id}/offers/confirm (crea prodotti nuovi se necessari)
                         │
                         ▼
  Frontend pubblico: GET /products / GET /flyers/public
```

### 3. Push notification su nuova offerta

```
  Browser utente: richiede permesso notifiche
  Browser: genera subscription {endpoint, p256dh, auth_key}
  Frontend: POST /push/subscribe
  Backend: salva in push_subscriptions (upsert per user_id + endpoint)
                         │
  Admin o manager conferma nuove offerte
  INSERT into offers ──▶ Supabase trigger
                         │
                         ▼
  Webhook: POST /push/notify-favorites  (X-Webhook-Secret)
    Backend: legge product_id + supermarket_name dalla nuova offerta
    Backend: cerca utenti che hanno quel prodotto tra i preferiti
    Per ogni utente:
      → insert app_notifications.favorite_offer
      → fetch push_subscriptions esistenti
      → per ogni subscription:
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
                    │
  Ora entrambi vedono la lista in tempo reale
  (SSE backend su /lists/{id}/events, alimentato da Postgres NOTIFY)
```

### 5. Freshness delle offerte in lista

```
  Utente aggiunge item con pinned_offer_id (da ottimizzazione o ricerca)
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
