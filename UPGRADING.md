# Migrating from the pre-release code

This guide is only for projects that used FlexCommerce from GitHub before the first PyPI release (1.0.0).
New projects can skip it.

1.0.0 changes data models, money semantics and some API responses. Plan the migration; don't just reinstall.

## 1. Databases

The pre-release code shipped migrations only for `flexcommerce_core`; every other app's tables were created by migrations your
project generated itself. 1.0.0 ships real migrations, and the models changed substantially.

- **New projects / no production data yet:** drop the FlexCommerce tables (or the database) and run
  `python manage.py migrate`. This is the recommended path.
- **Existing data:** export what you need (orders, customers' addresses, coupons, products) first. Remove the
  migration files your project generated for FlexCommerce apps (`MIGRATION_MODULES` or files inside
  site-packages), drop those apps' tables, run `migrate`, then re-import. `flexcommerce_core` keeps its original
  `0001_initial` and upgrades in place via `0002`.

## 2. Settings

- `AUTH_USER_MODEL` is now honoured — nothing to do unless you worked around the old `auth.User` references.
- `PRODUCT_MODELS` defaults to the catalog's `ProductVariant` when `flexcommerce_catalog` is installed; keep
  setting it if you use your own product model (it must still have a UUID primary key).
- Add `"rest_framework"` and the new apps you want to `INSTALLED_APPS`; `from flexcommerce import FLEXCOMMERCE_APPS`
  lists them in dependency order.
- Schedule `python manage.py flexcommerce_run_jobs` (replaces separate cron lines for `expire_carts`,
  `reconcile_inventory`, `aggregate_analytics` and `retry_failed_notifications`, which still exist).
- Replace `ASYNC_EXECUTOR = "celery"` wrappers: FlexCommerce now ships its own Celery task; just call
  `app.autodiscover_tasks()`.

## 3. Money semantics

- Cart and order totals are VAT-**inclusive** amounts (what the customer pays). In the pre-release code, with
  `VAT_INCLUSIVE = False`, `total` excluded VAT and orders were undercharged.
- `Cart.subtotal` / `total` / `tax_total` are stored fields (`subtotal_amount`, …) exposed under the same names.
- `CartItem.line_total` is now gross (incl. VAT) before discount.

## 4. API changes

- Errors are always `{"error", "detail", "extra"}` with meaningful status codes (e.g. 409 for conflicts, 402
  for payment problems, 404 for missing products) instead of 400 for everything.
- `product_model` is still accepted but `product_type` is preferred; unknown models are rejected.
- `GET /cart/` no longer creates a cart; responses include `cart_token`.
- Checkout replays with the same `idempotency_key` return the original order (200) instead of 409.
- Lists are paginated.
- `POST /reviews/` now takes `product_id` (and `product_type` when several models are reviewable).
- Order statuses gained `partially_shipped`; payment statuses gained `partially_refunded`.

## 5. Code

- `flexcommerce_core.utils.helpers.load_model`, `get_product_models`, `registry`, `executor`,
  `StateMachine`, `compute_tax` and the pricing handler classes keep their original import paths.
- `NotificationDispatcher.dispatch/dispatch_all/dispatch_for_user` still work.
- Signals are now sent after commit. Receivers that relied on running inside the transaction should become
  hooks (see [docs/events.md](docs/events.md)).
- `Order.transition()` now routes through `OrderService`, so cancellations release stock and restore coupons.
