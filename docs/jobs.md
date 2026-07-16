# Scheduled Jobs

## Background Jobs

The backend runs scheduled background jobs via APScheduler (`AsyncIOScheduler`), started in the FastAPI lifespan context manager in `main.py`.

- `flyer_cleanup` runs daily at 00:00 Europe/Rome and deletes offers linked to expired flyers, while keeping flyer rows/files for admin history.
- `purchased_items_cleanup` runs daily at 00:00 Europe/Rome and removes purchased list items from previous Rome days, resetting the "Acquistati oggi" section automatically without touching purchase history.

### Note storico acquisti

- `purchase_history.product_id` resta valorizzabile come snapshot storico del prodotto acquistato, ma non mantiene più una foreign key verso `products`.
- `purchase_history.quantity` salva quantità acquistata; `price_paid`, `price_original` e `savings` nello storico sono importi totali già scalati per quantità.
- `purchase_history` salva anche snapshot di `brand`, `format_label`, `image_url`, `category`, `subcategory` e dei campi `unit_price*`, così lo storico frontend mantiene stessa densità informativa anche se catalogo o offerte cambiano nel tempo.
- Questo permette di eliminare prodotti canonici non più usati senza perdere coerenza nello storico acquisti.

| Job | Schedule | Service | Description |
|-----|----------|---------|-------------|
| `flyer_cleanup` | Daily at 00:00 Europe/Rome | `services/flyer_cleanup.py` | Deletes offers linked to flyers where `valid_to < today`, but keeps the flyer row and uploaded file for historical/admin consultation. Flyers with `valid_to = NULL` are never auto-cleaned. |
| `purchased_items_cleanup` | Daily at 00:00 Europe/Rome | `services/purchased_items_cleanup.py` | Removes from each shopping list all items already purchased on previous Rome days. Items still purchased today stay visible in "Acquistati oggi" until midnight. Purchase history is not deleted. |

To trigger cleanup manually (ops or testing):

```bash
curl -X POST http://localhost:8000/flyers/admin/cleanup \
  -H "Authorization: Bearer <admin-jwt>"
# {"deleted": N}
```

---
