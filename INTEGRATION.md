# FlexCommerce Integration Guide

This guide takes a Django project from zero to taking real orders. Every step after 1–4 is optional —
enable only the apps you need.

## 1. Install

```bash
pip install "flexcommerce[all]"        # everything, incl. marketplace, Celery and FCM extras
# or pick apps:
pip install flexcommerce-cart flexcommerce-checkout flexcommerce-payments flexcommerce-shipping
```

## 2. Settings

```python
from flexcommerce import FLEXCOMMERCE_APPS, MARKETPLACE_APPS

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages",
    "rest_framework",
    *FLEXCOMMERCE_APPS,
    # *MARKETPLACE_APPS,                   # multi-vendor
]

MIDDLEWARE = [
    ...,
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "flexcommerce_cart.middleware.CartMiddleware",   # optional: lazy request.cart
]

FLEXCOMMERCE = {
    # Money & tax
    "CURRENCY": "NGN", "CURRENCY_SYMBOL": "₦", "VAT_RATE": 0.075,
    "VAT_INCLUSIVE": True,              # catalogue prices already include VAT (typical in Nigeria)
    # Payments
    "PAYSTACK_SECRET_KEY": env("PAYSTACK_SECRET_KEY"),
    "PAYMENT_CALLBACK_URL": "https://shop.example.com/checkout/complete",
    "PAYMENT_ALLOWED_CALLBACK_HOSTS": ["shop.example.com"],
    "BANK_TRANSFER_ACCOUNTS": [{"bank_name": "GTBank", "account_name": "Shop Ltd", "account_number": "0123456789"}],
    "PAY_ON_DELIVERY_LIMIT": 300000,
    # Notifications
    "STORE_NAME": "Naija Mart", "FRONTEND_URL": "https://shop.example.com",
    "NOTIFICATION_SMS_BACKEND": "flexcommerce_notifications.backends.sms.TermiiSMSBackend",
    "TERMII_API_KEY": env("TERMII_API_KEY"), "TERMII_SENDER_ID": "NaijaMart",
    # Background work (use "sync" in development)
    "ASYNC_EXECUTOR": "celery",
}
DEFAULT_FROM_EMAIL = "Naija Mart <orders@shop.example.com>"
```

`python manage.py check` validates the FlexCommerce configuration (unknown product models, missing Celery,
bad VAT rate, process-local cache in production, …).

Every option is documented in [docs/settings.md](docs/settings.md).

## 3. URLs

```python
urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("flexcommerce_core.urls")),   # every installed FlexCommerce app, nothing else
]
```

You may instead include a single app's `urls` (e.g. `include("flexcommerce_cart.urls")`).

## 4. Database

```bash
python manage.py migrate
```

Custom user models (`AUTH_USER_MODEL`) are fully supported.

## 5. Products

**Option A — use the catalog app** (default). Create categories, brands and products in the admin or via
`POST /api/catalog/products/`. The purchasable unit is `ProductVariant` (a product with one size/colour simply
has one variant):

```json
POST /api/catalog/products/
{"name": "Tecno Camon 30", "status": "active",
 "variants": [{"sku": "CAMON30-BLK", "price": "250000", "attributes": {"color": "Black"}}]}
```

**Option B — bring your own product model.** Any model with a UUID primary key and a `price` works:

```python
FLEXCOMMERCE = {"PRODUCT_MODELS": ["shop.Product"]}
```

Optional attributes FlexCommerce understands: `name`, `sku`, `is_active` / `is_purchasable`, `vat_exempt`,
`tax_category`, `vendor_id`, `weight`, `category_ids`, `stock` (see
[docs/extending.md](docs/extending.md#using-your-own-product-model)).

## 6. Stock

With `flexcommerce_inventory`, start tracking a product with
`POST /api/inventory/ {"product_id": "...", "on_hand": 50, "reorder_point": 5}` (or `import_inventory stock.csv`).
Products without an inventory record are unlimited unless `INVENTORY_TRACK_BY_DEFAULT = True`.

## 7. Shipping

```bash
python manage.py seed_nigeria_shipping --with-methods
```

creates the 8 geopolitical zones (all 36 states + FCT mapped) and a door-delivery method per zone. Edit rates,
add pickup stations or define your own zones/countries in the admin. Checkout requires a delivery method when
the shipping app is installed (`CHECKOUT_REQUIRE_SHIPPING`).

## 8. The customer journey (API)

```http
GET  /api/catalog/products/?category=phones&min_price=50000&ordering=price
POST /api/cart/add/                       {"product_id": "<variant id>", "quantity": 1}
POST /api/cart/coupon/                    {"code": "NAIJA10"}
POST /api/checkout/shipping-options/      {"state": "Lagos"}
POST /api/checkout/preview/               {"shipping_address": {...}, "shipping_method_id": "..."}
POST /api/checkout/                       (below)
GET  /api/payments/verify/?reference=...  (after the gateway redirects back)
GET  /api/orders/{id}/                    timeline, shipments, refunds
```

```json
POST /api/checkout/
{
  "payment_method": "paystack",
  "shipping_address": {"first_name": "Ifeanyi", "last_name": "Nnamani", "phone": "08012345678",
                       "line1": "123 GRA", "landmark": "Opposite Presidential Hotel",
                       "city": "Port Harcourt", "lga": "Obio/Akpor", "state": "Rivers"},
  "shipping_method_id": "…",
  "idempotency_key": "a-uuid-generated-by-the-client",
  "expected_total": "261500.00",
  "callback_url": "https://shop.example.com/checkout/complete"
}
```

The response contains the order and, for online payments, `redirect_url`. Checkout is atomic and idempotent:
retrying with the same `idempotency_key` returns the same order (HTTP 200). If prices changed since the customer
saw them, `expected_total` makes checkout fail with `price_changed` (409) instead of charging a different amount.

**Guests** can check out with an `email`; they get an `access_token` for
`GET /api/orders/guest/{order_number}/?token=...` and can also use `POST /api/orders/track/`.

**Mobile / SPA clients** without cookies send the `cart_token` from any cart response back in the
`X-Cart-Token` header. After login, that anonymous cart merges into the customer's cart automatically.

## 9. Payments

| Method | Key | How it settles |
|---|---|---|
| Paystack | `paystack` | Redirect → webhook + `/payments/verify/` (signature and amount verified) |
| Flutterwave | `flutterwave` | Redirect → webhook (`verif-hash`) + verify |
| Bank transfer | `bank_transfer` | Customer sees your account details; staff call `POST /orders/{id}/mark-paid/` |
| Pay on delivery | `pay_on_delivery` | Order confirmed immediately (stock committed); mark paid on delivery |
| Wallet | `wallet` | Debited atomically inside the checkout transaction |

Register webhook URLs in your gateway dashboard:
`https://shop.example.com/api/payments/webhooks/paystack/` and `.../flutterwave/`.
Refunds processed from the admin/API go back through the same gateway (or to the wallet).

## 10. Background jobs

One cron line (or Celery beat) keeps everything healthy:

```cron
*/5 * * * * python manage.py flexcommerce_run_jobs
```

| Job | Every | Does |
|---|---|---|
| `inventory.release_expired` | 5 min | Releases stock held by unpaid orders |
| `orders.expire_unpaid` | 5 min | Cancels unpaid online/bank-transfer orders after their deadline (restores stock, coupons, flash-sale units) |
| `cart.abandoned` | 15 min | Emits `cart.abandoned` (→ recovery emails) |
| `cart.expire` | 60 min | Expires and purges stale anonymous carts |
| `notifications.retry` / `webhooks.retry` | 1–2 min | Retries failed deliveries with backoff |
| `payments.expire_stale` | 60 min | Re-verifies and abandons long-pending payments |
| `engagement.price_drops` | 6 h | Wishlist price-drop alerts |
| `analytics.daily_summary` | 60 min | Rebuilds daily revenue summaries |
| `marketplace.payouts` | daily | Creates vendor payouts after the return window |

`python manage.py flexcommerce_run_jobs --list` shows them; `--force` runs everything now.

## 11. Notifications

Installing `flexcommerce_notifications` is all it takes: order, payment, shipping, delivery, refund, return,
abandoned-cart, price-drop, back-in-stock, vendor and staff messages are sent automatically on email, SMS,
push and the in-app inbox, respecting each customer's preferences. Edit any message in the admin
(**Notification templates**) using Django template syntax. See [docs/events.md](docs/events.md#notifications).

## 12. Marketplace

Add `flexcommerce_marketplace`. Sellers apply with `POST /api/vendors/apply/`; staff approve them. Approved
vendors manage their own products through the catalog API, see only their own items in
`/api/vendors/me/orders/`, ship them, and receive payouts (sales − commission − refunds) after the return window.

## 13. Going to production

- `ASYNC_EXECUTOR = "celery"` (or `"threading"`) so slow providers never slow checkout.
- A shared cache (Redis) for throttling and job locks; PostgreSQL for the database.
- Schedule `flexcommerce_run_jobs`.
- Keep `WEBHOOK_REQUIRE_HTTPS = True` and `WEBHOOK_ALLOW_PRIVATE_URLS = False`.
- Set `PAYMENT_ALLOWED_CALLBACK_HOSTS`.
- Read [docs/operations.md](docs/operations.md) for the full checklist.
