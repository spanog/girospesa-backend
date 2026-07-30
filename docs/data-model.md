# Data Model

## Schema and RLS

- `analytics_data` ed `extraction_log` sono tabelle interne. RLS resta abilitato con policy esplicite `deny all`; accesso e scrittura passano solo dal backend con `SUPABASE_SECRET_KEY`.
- `manager_supermarkets` e `flyer_targets` sono tabelle di coordinamento backend/admin per gestione multi-branch e pubblicazione volantini. Anche qui RLS resta attivo con policy esplicite `deny all`; lettura e scrittura passano dal backend con `SUPABASE_SECRET_KEY`.
- Le richieste contatto, bug, collaborazione e volantini mancanti passano da `POST /contact-requests` con invio email al webmaster. Non esiste più persistenza applicativa su tabella `flyer_requests`.
- Log estrazione canonico: `extraction_log`. Eventuali ambienti locali legacy con `scraping_log` vengono riallineati dalla migration di hardening.
- PostGIS è abilitato nello schema `extensions`. `supermarkets.location`, `user_profiles.home_location` e `user_profiles.search_location` sono `geography(Point, 4326)` indicizzate GiST; la RPC `nearby_supermarkets` usa `ST_DWithin` e `ST_Distance`.
- Gli helper RLS per le liste condivise vivono nello schema non esposto `private` (`private.is_list_member`, `private.is_list_owner`). Restano `SECURITY DEFINER` per evitare ricorsione nelle policy, ma non sono endpoint RPC pubblici su `/rest/v1/rpc/*`.
- Nelle policy RLS che usano helper Auth Supabase, preferire `(select auth.uid())` e `(select auth.jwt())` al posto della chiamata inline: stessa semantica, meno lavoro per riga, niente warning `auth_rls_initplan`.
- Il trigger DB di signup `public.handle_new_user()` crea `user_profiles` copiando `display_name` e campi indirizzo (`home_address`, `home_city`, `home_province`, `home_postal_code`) da `raw_user_meta_data`, poi crea la lista owner `La mia lista` e la relativa membership `owner`.

## Supabase Storage Buckets

| Bucket | Pattern path | Scopo | Max dimensione | Accesso |
|--------|-------------|-------|----------------|---------|
| `avatars` | `{user_id}.{jpg\|png\|webp}` | Foto profilo utente | 5 MB | URL pubblico |
| `flyers` | `{user_id}/{uuid}.{pdf\|jpg}` e `previews/{flyer_id}.webp` | Volantini caricati e thumbnail WebP server-side | 50 MB per file sorgente | Privato, accesso backend/service role |
| `product-images` | `draft-offers/{offer_id}/auto-packshot.png` o `draft-offers/{offer_id}/{uuid}.{ext}` | Crop estratti dal volantino e immagini caricate in review | — | URL pubblico |

I bucket pubblici non espongono listing anonimo via `storage.objects`: client e frontend devono usare solo URL diretti `/storage/v1/object/public/...`.

---

## Schema Analytics B2B

La tabella `analytics_data` contiene metriche aggregate e anonimizzate per le catene GDO:

| Campo | Tipo | Descrizione |
|-------|------|-------------|
| `id` | UUID | PK |
| `week_start` | date | Settimana di riferimento |
| `metric_type` | string | `offer_efficacia`, `categoria_trend`, `conversion_rate`, `sconto_benchmark` |
| `category` | string | Categoria prodotto (opzionale) |
| `supermarket_id` | string | Supermercato (opzionale) |
| `value` | float | Valore della metrica |
| `description` | text | Note opzionali |
| `created_at` | timestamptz | Timestamp inserimento |

Definizione inclusa nella baseline `supabase/migrations/20260617000000_initial_schema.sql`. I dati sono sempre anonimi e GDPR-compliant — nessuna informazione personale.
RLS resta abilitato anche su questa tabella; accesso previsto solo tramite backend/service role.
