# Scheduled Jobs

## Background Jobs

The backend runs scheduled background jobs via APScheduler (`AsyncIOScheduler`), started in the FastAPI lifespan context manager in `main.py`.

- `flyer_cleanup` runs daily at 00:00 Europe/Rome and deletes each expired source flyer completely: the source row, published target copies and offers cascade in the database; its PDF/image source, preview and unshared `product-images` crops are removed from Storage.
- `purchased_items_cleanup` runs daily at 00:00 Europe/Rome and removes purchased list items from previous Rome days, resetting the "Acquistati oggi" section automatically without touching purchase history.
- `notification_jobs` runs every minute and drains queued publication notifications created by offer confirmation or target-publication sync. For a future flyer, the parent job waits until 10:00 `Europe/Rome` on its `valid_from`; changing that date reschedules only jobs not yet delivered. Parent jobs materialize every admin, the manager assigned to the published supermarket, and nearby customers; child deliveries run in bounded batches and are retried independently before moving to `dead`. Jobs left in `processing` by a terminated worker return to `pending` after the configured lock timeout, unless their final attempt was already consumed; those transition to `dead`.

### Note storico acquisti

- `purchase_history.product_id` è un campo di compatibilità e viene salvato a `NULL`; non mantiene una foreign key verso un catalogo prodotti.
- `purchase_history.quantity` salva quantità acquistata; `price_paid`, `price_original` e `savings` nello storico sono importi totali già scalati per quantità.
- `purchase_history` salva anche snapshot di `brand`, `format_label`, `image_url`, `category`, `subcategory` e dei campi `unit_price*`, così lo storico frontend resta coerente anche quando un'offerta non è più disponibile.

| Job | Schedule | Service | Description |
|-----|----------|---------|-------------|
| `flyer_cleanup` | Daily at 00:00 Europe/Rome | `services/flyer_cleanup.py` | Deletes source flyers where `valid_to < today`. Database cascades remove published target copies and linked offers; the service then deletes their original file, generated preview and `product-images` objects not referenced by another offer through the Storage API. Flyers with `valid_to = NULL` are never auto-cleaned. |
| `purchased_items_cleanup` | Daily at 00:00 Europe/Rome | `services/purchased_items_cleanup.py` | Removes from each shopping list all items already purchased on previous Rome days. Items still purchased today stay visible in "Acquistati oggi" until midnight. Purchase history is not deleted. |
| `notification_jobs` | Every minute | `services/notification_jobs.py` | Claims up to `NOTIFICATION_DELIVERY_BATCH_SIZE` parent and child jobs, resolves recipients, then delivers children with at most `NOTIFICATION_DELIVERY_WORKERS` threads. Defaults are 10 jobs, 2 threads, 10-second push deadlines, and a 10-minute stale-lock recovery window. Inbox is always persisted; Web Push/native FCM requires `notifications_enabled=true`. Failures retry per recipient without blocking flyer publication. |

For a one-off backfill of existing orphan crops, run `python -m scripts.remove_orphan_offer_images` first, then repeat with `--apply` only after reviewing the candidate count. If local development uses a different Supabase project, pass the production dotenv path with `--env-file /secure/path/.env.production`.

To trigger cleanup manually (ops or testing):

```bash
curl -X POST http://localhost:8000/flyers/admin/cleanup \
  -H "Authorization: Bearer <admin-jwt>"
# {"deleted": N}
```

To drain notification jobs manually:

```bash
curl -X POST http://localhost:8000/ops/cron/notifications \
  -H "X-Ops-Secret: <ops-secret>"
# {"status":"ok","claimed":N,"processed":N,"failed":0}
```

---
