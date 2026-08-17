# Scheduled Jobs

## Background Jobs

The backend runs scheduled background jobs via APScheduler (`AsyncIOScheduler`), started in the FastAPI lifespan context manager in `main.py`.

- `flyer_cleanup` runs daily at 00:00 Europe/Rome and deletes offers linked to expired flyers, while keeping flyer rows/files for admin history.
- `purchased_items_cleanup` runs daily at 00:00 Europe/Rome and removes purchased list items from previous Rome days, resetting the "Acquistati oggi" section automatically without touching purchase history.
- `notification_jobs` runs every minute and drains queued publication notifications created by offer confirmation or target-publication sync. Parent jobs materialize every admin, the manager assigned to the published supermarket, and nearby customers; child deliveries run in a bounded thread pool and are retried independently before moving to `dead`.

### Note storico acquisti

- `purchase_history.product_id` è un campo di compatibilità e viene salvato a `NULL`; non mantiene una foreign key verso un catalogo prodotti.
- `purchase_history.quantity` salva quantità acquistata; `price_paid`, `price_original` e `savings` nello storico sono importi totali già scalati per quantità.
- `purchase_history` salva anche snapshot di `brand`, `format_label`, `image_url`, `category`, `subcategory` e dei campi `unit_price*`, così lo storico frontend resta coerente anche quando un'offerta non è più disponibile.

| Job | Schedule | Service | Description |
|-----|----------|---------|-------------|
| `flyer_cleanup` | Daily at 00:00 Europe/Rome | `services/flyer_cleanup.py` | Deletes offers linked to flyers where `valid_to < today`, but keeps the flyer row and uploaded file for historical/admin consultation. Flyers with `valid_to = NULL` are never auto-cleaned. |
| `purchased_items_cleanup` | Daily at 00:00 Europe/Rome | `services/purchased_items_cleanup.py` | Removes from each shopping list all items already purchased on previous Rome days. Items still purchased today stay visible in "Acquistati oggi" until midnight. Purchase history is not deleted. |
| `notification_jobs` | Every minute | `services/notification_jobs.py` | Claims parent jobs, resolves all admins, the assigned manager, and nearby customers, then delivers child jobs in parallel. Inbox is always persisted; Web Push/native FCM requires `notifications_enabled=true`. Failures retry per recipient without blocking flyer publication. |

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
