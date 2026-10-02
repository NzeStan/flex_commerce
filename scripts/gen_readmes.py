"""Generate each package's README.md (shown on PyPI). Edit the content here."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = "https://github.com/NzeStan/flex_commerce/blob/main"

PACKAGES = {
    "core": (
        "Foundation of FlexCommerce: settings, post-commit events, in-transaction hooks, background execution, "
        "maintenance jobs, signed outgoing webhooks, audit log, address book and the shared API conventions.",
        [
            "One `FLEXCOMMERCE = {...}` settings dict; every package registers its defaults",
            "`events.emit()` — signals that fire only after the transaction commits, isolated from each other",
            "`hooks` — plugins that take part in the business operation (and can veto it)",
            "`tasks.enqueue()` — `sync`, `threading`, Celery or your own executor",
            "`flexcommerce_run_jobs` — one command for every package's maintenance jobs",
            "Outgoing webhooks: HMAC-SHA256 signatures, retries with backoff, SSRF protection",
            "Address book API (Nigeria-aware: LGA, landmark, phone validation)",
            "Consistent error format, pagination and throttling for every FlexCommerce API",
            "System checks that catch misconfiguration at `manage.py check`",
            '`pytest_plugins = ["flexcommerce_core.testing"]` helpers for your own tests',
        ],
        "GET/POST /api/addresses/",
    ),
    "catalog": (
        "Product catalogue: category tree, brands, products with variants (size, colour…), images, search, "
        "filters, facets and related products. Approved marketplace vendors manage their own products.",
        [
            "Materialised-path category tree with cached nested endpoint",
            "Products → variants (the purchasable unit, each with SKU, price, old price, weight, attributes)",
            "Filters: category (incl. children), brand, price, rating, in stock, on sale, attributes; 7 sort orders",
            "Pluggable search (database, PostgreSQL full-text, or Elasticsearch/Meilisearch via a function)",
            "Denormalised price range, stock status, rating and units sold for fast listings",
            "Facets endpoint (price range + brand counts) for filter sidebars",
        ],
        "GET /api/catalog/products/?category=phones&brand=tecno&ordering=price",
    ),
    "pricing": (
        "Tax categories (standard / zero-rated / exempt / custom rate), a VAT calculator and authoritative price "
        "quotes that include flash sales and tax.",
        [
            "Admin-managed tax categories used by the price pipeline",
            "`POST /pricing/calculate/` VAT breakdown, inclusive or exclusive",
            "`POST /pricing/quote/` server-side prices for any products",
        ],
        "POST /api/pricing/quote/",
    ),
    "inventory": (
        "Concurrency-safe stock for any product model: atomic reservations that expire, sales, returns, "
        "adjustments, an audited movement log, CSV import and back-in-stock alerts.",
        [
            "Reservations via a single conditional UPDATE — the last unit can never be sold twice",
            "Reservations expire automatically (unpaid orders) and convert to sales on payment",
            "Low / out-of-stock / restocked signals that fire when the level crosses the threshold",
            '"Notify me when available" alerts for customers and guests',
            "`import_inventory stock.csv` and `reconcile_inventory` commands",
        ],
        "POST /api/inventory/{id}/restock/",
    ),
    "discounts": (
        "Coupons, automatic promotions and flash sales — with usage limits that hold under heavy traffic.",
        [
            "Percentage (with cap), fixed, free shipping and buy-X-get-Y coupons",
            "Product, category, exclusion and vendor restrictions; minimum spend; first order only",
            "Global and per-customer (incl. guest email) limits enforced atomically at checkout",
            "Automatic code-less promotions (best one wins)",
            'Flash sales with per-product sale prices and "only N left" caps',
        ],
        "POST /api/cart/coupon/",
    ),
    "shipping": (
        "Shipping for Nigeria out of the box (36 states + FCT in 8 zones) and configurable for anywhere: flat, "
        "per-item and weight-tier rates, free-shipping thresholds, pickup stations and delivery estimates.",
        [
            "`seed_nigeria_shipping --with-methods` sets everything up",
            "Custom zones by state list or country",
            "Pickup stations with their own fees",
            "Pay-on-delivery availability per method",
            "Delivery date estimates that skip Sundays",
        ],
        "POST /api/shipping/methods/calculate/",
    ),
    "cart": (
        "Carts for signed-in users, browser sessions and cookie-less mobile apps (`X-Cart-Token`), re-priced on "
        "every change with VAT, coupons and automatic promotions, merged automatically at login.",
        [
            "No database rows for visitors who never add anything",
            "Totals stored on the cart for cheap reads; stale products removed with a notice",
            "Quantity and line limits, stock checks, save for later",
            "Abandoned-cart detection (drives recovery emails) and purging of old carts",
            "Optional lazy `request.cart` middleware",
        ],
        "POST /api/cart/add/",
    ),
    "orders": (
        "The full order lifecycle: payment, confirmation, partial shipments with tracking events, delivery, "
        "returns within a return window, refunds, guest order tracking and a customer-visible timeline.",
        [
            "Every state change goes through one service with row locking and side effects (stock, coupons)",
            "Unpaid orders expire and release stock automatically",
            "Refunds validated against what was paid, paid out via the original gateway or wallet",
            "Returns with reasons, photos, approval, receipt (restock) and refund",
            "Guest tracking by order number + email, or a secret link",
            "`export_orders` CSV command",
        ],
        "POST /api/orders/{id}/return/",
    ),
    "payments": (
        "Payments for Nigeria and Africa: Paystack, Flutterwave, bank transfer, pay on delivery and a customer "
        "wallet — with verified webhooks and a pluggable gateway interface.",
        [
            "Every payment re-verified with the provider; amount and currency checked before an order is paid",
            "Signature-verified, deduplicated webhooks",
            "Refunds through the same gateway or to the wallet",
            "Wallet with an idempotent, never-negative ledger and top-ups",
            "Add Stripe, Monnify, Opay… by writing one class",
        ],
        "POST /api/payments/initiate/",
    ),
    "checkout": (
        "One atomic, idempotent checkout: re-prices the cart, validates delivery for the address, reserves stock, "
        "redeems the coupon, creates the order and starts the payment — safe against double submits and races.",
        [
            "Idempotency keys: retries return the original order",
            "`expected_total` guard against silent price changes",
            "Guest checkout, saved addresses, pickup stations, wallet payments",
            "Preview and shipping-options endpoints for checkout pages",
            "Works with only the cart and orders apps installed; uses shipping, inventory, discounts and payments when present",
        ],
        "POST /api/checkout/",
    ),
    "engagement": (
        "Wishlists, reviews and ratings, product Q&A, recently viewed products and search history with trending "
        "searches.",
        [
            "Default wishlist, heart toggle, sharing, move to cart, price-drop alerts",
            "Reviews with verified-purchase badges, moderation, one helpful vote per user, seller replies",
            "Rating summaries with 1–5 star distribution (kept on catalog products automatically)",
            "Product questions and official answers",
            "Guest browsing history merged into the account at login",
        ],
        "GET /api/reviews/summary/?product_id=…",
    ),
    "marketplace": (
        "Turn a store into a Jumia/Konga-style marketplace: seller onboarding, per-vendor order splitting, "
        "commissions, vendor fulfilment, refund charge-backs and payouts after the return window.",
        [
            "Vendor applications with KYC and bank details; staff approval",
            "Orders split per vendor inside the checkout transaction, commission snapshotted",
            "Vendors manage their own catalog products and ship their own items",
            "Refunds charged back to the right vendor",
            "Payouts generated once the hold period passes",
        ],
        "POST /api/vendors/me/orders/{id}/ship/",
    ),
    "notifications": (
        "Signal-driven notifications on email, SMS, push and an in-app inbox — with an outbox, retries, "
        "deduplication, per-user preferences and admin-editable templates.",
        [
            "Zero wiring: order, payment, shipping, refund, cart-recovery, stock, vendor and staff messages",
            "SMS: Termii, Africa's Talking, Twilio, Kudisms; push: FCM HTTP v1, OneSignal",
            "Default messages for every event; override per channel in the admin",
            "Per-event switches and channel routing in settings",
            "In-app inbox and device-token APIs",
        ],
        "GET /api/notifications/inbox/",
    ),
    "analytics": (
        "Staff reports for revenue, products, carts, coupons, vendors and customers, a KPI dashboard and CSV exports.",
        [
            "Revenue by day/week/month per currency (gross, net, VAT, shipping, discounts, collected, refunds)",
            "Top products, abandoned carts and recovery, conversion, coupon performance",
            "Sales by state, payment-method mix, new vs returning customers",
            "Pre-aggregated daily summaries; formula-injection-safe CSV",
        ],
        "GET /api/analytics/dashboard/",
    ),
}

TEMPLATE = """# {dist}

{summary}

Part of **[FlexCommerce]({docs}/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

{features}

## Install

```bash
pip install {dist}
```

```python
INSTALLED_APPS = [..., "rest_framework", {apps}]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `{example}`

## Documentation

- [Integration guide]({docs}/INTEGRATION.md)
- [Settings]({docs}/docs/settings.md) · [API]({docs}/docs/api.md) · [Events & notifications]({docs}/docs/events.md)
- [Extending]({docs}/docs/extending.md) · [Operations]({docs}/docs/operations.md) · [Changelog]({docs}/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
"""

DEPENDS = {
    "payments": ["orders"],
    "checkout": ["cart", "orders"],
    "marketplace": ["orders"],
}


def main():
    for name, (summary, features, example) in PACKAGES.items():
        apps = ["flexcommerce_core"] + [f"flexcommerce_{d}" for d in DEPENDS.get(name, [])]
        if name != "core":
            apps.append(f"flexcommerce_{name}")
        text = TEMPLATE.format(
            dist=f"flexcommerce-{name}",
            summary=summary,
            docs=DOCS,
            example=example,
            features="\n".join(f"- {f}" for f in features),
            apps=", ".join(f'"{a}"' for a in apps),
        )
        (ROOT / f"flexcommerce-{name}" / "README.md").write_text(text, encoding="utf-8")
    meta = (ROOT / "README.md").read_text(encoding="utf-8").replace("](docs/", f"]({DOCS}/docs/")
    for page in ("INTEGRATION.md", "UPGRADING.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md"):
        meta = meta.replace(f"]({page})", f"]({DOCS}/{page})")
    meta = meta.replace("](docs/)", f"]({DOCS}/docs/)")
    (ROOT / "flexcommerce-meta" / "README.md").write_text(meta, encoding="utf-8")


if __name__ == "__main__":
    main()
