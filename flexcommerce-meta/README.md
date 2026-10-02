# FlexCommerce

**A complete, modular e-commerce and marketplace backend for Django + DRF — built for Nigeria and Africa, ready for anywhere.**

FlexCommerce gives you everything behind a Jumia- or Konga-style store as a set of optional Django apps:
catalogue, cart, checkout, orders, payments (Paystack, Flutterwave, bank transfer, pay on delivery, wallet),
inventory, promotions, shipping with pickup stations, reviews and Q&A, multi-vendor marketplace, notifications
(email, SMS, push, in-app), outgoing webhooks and analytics. You bring the frontend (web, mobile, POS) — FlexCommerce
is **strictly backend**: a REST API, Django admin, signals, hooks and management commands.

```bash
pip install "flexcommerce[all]"
```

- **Every feature is optional.** Install one app or all fourteen; each works with only its declared dependencies.
- **Safe under load.** Stock reservations, coupon limits, flash-sale caps, wallet debits and checkout are
  race-free (conditional updates + row locks, verified by concurrent tests on PostgreSQL).
- **Correct money.** VAT-inclusive or exclusive pricing, discounts allocated per line, server-side re-pricing at
  checkout, amount + currency verification on every payment.
- **Pluggable.** Swap the price/tax handler, search engine, payment gateways, notification providers and order
  number format through settings; extend behaviour with in-transaction hooks; react to post-commit signals or
  signed JSON webhooks.
- **Tested.** ~630 tests across Python 3.10–3.14 and Django 4.2 / 5.2 / 6.x on SQLite and PostgreSQL.

## Packages

| App (`INSTALLED_APPS`) | PyPI | What it does |
|---|---|---|
| `flexcommerce_core` | `flexcommerce-core` | Settings, events, hooks, jobs, signed webhooks, audit log, address book, API conventions |
| `flexcommerce_catalog` | `flexcommerce-catalog` | Categories, brands, products & variants, images, search, filters, facets |
| `flexcommerce_pricing` | `flexcommerce-pricing` | Tax categories, VAT calculator, authoritative price quotes |
| `flexcommerce_inventory` | `flexcommerce-inventory` | Atomic stock reservations, movements, CSV import, back-in-stock alerts |
| `flexcommerce_discounts` | `flexcommerce-discounts` | Coupons, automatic promotions, flash sales with quantity caps |
| `flexcommerce_shipping` | `flexcommerce-shipping` | Nigerian zones, flat / per-item / weight rates, pickup stations, ETAs |
| `flexcommerce_cart` | `flexcommerce-cart` | Session, token (mobile) and user carts, live pricing, save-for-later |
| `flexcommerce_orders` | `flexcommerce-orders` | Order lifecycle, partial shipments & tracking, returns, refunds, guest tracking |
| `flexcommerce_payments` | `flexcommerce-payments` | Paystack, Flutterwave, bank transfer, pay on delivery, customer wallet |
| `flexcommerce_checkout` | `flexcommerce-checkout` | One atomic, idempotent checkout tying everything together |
| `flexcommerce_engagement` | `flexcommerce-engagement` | Wishlists, reviews & ratings, Q&A, recently viewed, trending searches |
| `flexcommerce_marketplace` | `flexcommerce-marketplace` | Sellers, per-vendor order splitting, commissions, payouts |
| `flexcommerce_notifications` | `flexcommerce-notifications` | Email, SMS (Termii, Africa's Talking, Twilio, Kudisms), push (FCM v1, OneSignal), in-app |
| `flexcommerce_analytics` | `flexcommerce-analytics` | Revenue, products, carts, coupons, vendors, customers, CSV export |

`pip install flexcommerce` installs all of them except the marketplace (`flexcommerce[marketplace]` or `[all]`).

## Quick start

```python
# settings.py
from flexcommerce import FLEXCOMMERCE_APPS

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages",
    "rest_framework",
    *FLEXCOMMERCE_APPS,            # or list only the apps you want
]

FLEXCOMMERCE = {
    "VAT_INCLUSIVE": True,                         # prices you enter already include 7.5% VAT
    "PAYSTACK_SECRET_KEY": env("PAYSTACK_SECRET_KEY"),
    "PAYMENT_CALLBACK_URL": "https://shop.example.com/checkout/complete",
    "PAYMENT_ALLOWED_CALLBACK_HOSTS": ["shop.example.com"],
    "FRONTEND_URL": "https://shop.example.com",
    "STORE_NAME": "Naija Mart",
    "ASYNC_EXECUTOR": "celery",                    # "sync" is fine for development
}
```

```python
# urls.py
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]   # mounts every installed app
```

```bash
python manage.py migrate
python manage.py seed_nigeria_shipping --with-methods   # 8 geopolitical zones + starter rates
python manage.py check                                   # FlexCommerce validates its configuration
```

Schedule the maintenance jobs (expired reservations, unpaid orders, abandoned carts, retries, payouts, analytics):

```cron
*/5 * * * * cd /srv/shop && ./venv/bin/python manage.py flexcommerce_run_jobs
```

(or schedule the Celery task `flexcommerce.run_jobs` with Celery beat.)

A customer can now browse `GET /api/catalog/products/`, `POST /api/cart/add/`, `POST /api/checkout/` and pay.
See **[docs/](https://github.com/NzeStan/flex_commerce/blob/main/docs/)** for the full guide.

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md) — install, configure, first order, going to production
- [Settings reference](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) — every `FLEXCOMMERCE` option
- [API reference](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) — every endpoint
- [Events, hooks, webhooks & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md) — how the apps talk to each other and to you
- [Extending FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) — custom products, gateways, pricing, search, SMS providers
- [Operations & scaling](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) — jobs, Celery, caching, security checklist
- [Migrating from pre-release code](https://github.com/NzeStan/flex_commerce/blob/main/UPGRADING.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md) · [Security](https://github.com/NzeStan/flex_commerce/blob/main/SECURITY.md) · [Contributing](https://github.com/NzeStan/flex_commerce/blob/main/CONTRIBUTING.md)

## Requirements

Python 3.10+, Django 4.2 LTS / 5.x / 6.x, Django REST framework 3.14+. PostgreSQL is recommended in production
(MySQL 8 works; SQLite is fine for development). A shared cache (Redis / Memcached) is recommended when you run
more than one web process.

## License

MIT © NzeStan
