# Operations & scaling

## Production checklist

- [ ] PostgreSQL (or MySQL 8). SQLite cannot serve concurrent writers.
- [ ] Redis or Memcached as `CACHES["default"]` — throttling, job locks and the category/promotion caches are
      shared through it (`manage.py check` warns otherwise).
- [ ] `ASYNC_EXECUTOR = "celery"` (with `app.autodiscover_tasks()`), so emails, SMS and webhooks never run
      inside the customer's request (`check` warns about `"sync"` when `DEBUG = False`).
- [ ] `flexcommerce_run_jobs` scheduled every 5 minutes (cron) **or** the Celery beat task `flexcommerce.run_jobs`.
- [ ] Payment secrets from the environment; `PAYMENT_ALLOWED_CALLBACK_HOSTS` set; gateway webhooks registered.
- [ ] `DEFAULT_FROM_EMAIL` and an email provider configured; SMS/push backends configured if used.
- [ ] `WEBHOOK_REQUIRE_HTTPS = True`, `WEBHOOK_ALLOW_PRIVATE_URLS = False` (defaults).
- [ ] Django's own deployment checklist (`manage.py check --deploy`), HTTPS, `SECURE_*` settings.
- [ ] Database backups; `AUDIT_ENABLED` kept on.

## Celery

```python
# proj/celery.py
app = Celery("proj")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()          # registers flexcommerce.run and flexcommerce.run_jobs

app.conf.beat_schedule = {
    "flexcommerce-jobs": {"task": "flexcommerce.run_jobs", "schedule": 300.0},
}
```

Task arguments are only ids, and the notification/webhook outboxes keep their own retry state, so lost or
duplicated tasks are harmless.

## How concurrency is handled

| Risk | Protection |
|---|---|
| Overselling the last units | Conditional `UPDATE … WHERE on_hand >= reserved + qty`; reservations are all-or-nothing and processed in a fixed order (no deadlocks) |
| Double checkout (double tap, retries) | Cart row lock + unique `idempotency_key`; replays return the original order |
| Coupon over-use | Conditional `UPDATE … WHERE used_count < usage_limit` inside the checkout transaction; per-customer counters |
| Flash-sale over-selling | Conditional `UPDATE` on `sold_quantity` in the `order.created` hook |
| Double payment processing (webhook + redirect) | Payment row lock; settlement is idempotent per reference |
| Wallet overdraft | Conditional `UPDATE … WHERE balance >= amount`; ledger references are unique |
| Concurrent order changes (staff cancel vs. payment) | Every order state change locks the order row |
| Slow third parties | Gateway calls happen after commit; notifications and webhooks are async with timeouts |

All of these are exercised by concurrent tests on PostgreSQL (`integration_tests/test_concurrency.py`).

## Performance notes

- Product listings use denormalised `min_price`, `in_stock`, `rating_avg`, `sold_count` (kept current by
  signals) and a bounded number of queries per page.
- Carts store their totals; `GET /cart/` never creates rows for visitors without a cart.
- Cart lines, order items and analytics resolve products with one query per product type (no N+1).
- Analytics filter on indexed datetime ranges; `DailyRevenueSummary` pre-aggregates dashboards.
- Expired anonymous carts are purged after `CART_PURGE_DAYS`.

## Security notes

- Clients can only reference whitelisted product models; prices, discounts and shipping are always computed on
  the server; `expected_total` protects customers from silent price changes.
- Payments are verified server-to-server with amount and currency checks; webhook signatures are verified with
  constant-time comparison.
- Guest order access needs the order number **and** email (throttled) or a random access token.
- Staff-only endpoints use `is_staff`; customers only ever see their own orders, addresses, wishlists,
  reviews (edit/delete), payments and wallet; vendors only their own products and order lines.
- CSV exports neutralise spreadsheet formula injection.
- Outgoing webhooks refuse private/metadata IPs and never follow redirects.

## Management commands

| Command | |
|---|---|
| `flexcommerce_run_jobs [--list] [--force] [--only …]` | All maintenance jobs |
| `seed_nigeria_shipping [--with-methods]` | Zones and starter rates |
| `import_inventory stock.csv [--dry-run]` | `sku,on_hand[,reorder_point]` |
| `reconcile_inventory` | Release expired holds, list low stock |
| `expire_carts [--dry-run]` | Expire / purge carts, abandoned-cart events |
| `export_orders --output=orders.csv [--status --from-date --to-date]` | |
| `retry_failed_notifications [--channel --limit --dry-run]` | |
| `generate_payouts` | Vendor payouts |
| `aggregate_analytics [--start-date --end-date --rebuild]` | |
| `export_analytics --report revenue|top_products|…` | |
