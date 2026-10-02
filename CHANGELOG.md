# Changelog

All notable changes to FlexCommerce are recorded here. The project follows [Semantic Versioning](https://semver.org/).

## 1.0.0 — 2026-10-01

First public release on PyPI. Projects that ran the pre-release code from GitHub should read
[UPGRADING.md](UPGRADING.md).

### Packages
- **flexcommerce-core**: settings, base models, address book (with LGA and landmark), post-commit event bus,
  in-transaction hooks, signed outgoing webhooks, background executor, a jobs runner, and system checks.
- **flexcommerce-catalog**: categories, brands, products with variants and images, search, filters, facets.
- **flexcommerce-pricing**: VAT-inclusive and VAT-exclusive pricing, tax exemptions, and per-line pricing.
- **flexcommerce-inventory**: atomic stock reservations that expire, stock movements, adjustments, CSV import,
  low-stock and back-in-stock alerts.
- **flexcommerce-discounts**: coupons (percentage, fixed, free shipping, buy-X-get-Y), product and category
  restrictions, caps, first-order-only coupons, per-customer limits, vendor-funded coupons, automatic
  promotions, and flash sales with quantity caps.
- **flexcommerce-shipping**: Nigerian zones out of the box, custom zones and countries, weight tiers, pickup
  stations, delivery estimates.
- **flexcommerce-cart**: session and token carts (`X-Cart-Token`), merge at login, stored totals, limits,
  guest contact for abandoned-cart recovery.
- **flexcommerce-orders**: a status state machine, guest tracking, a customer timeline, partial shipments with
  tracking events, returns with a return window, and refunds through the original gateway or the wallet. Also
  unpaid-order expiry and CSV export.
- **flexcommerce-payments**: Paystack, Flutterwave, bank transfer, pay on delivery and a customer wallet, with
  server-to-server verification.
- **flexcommerce-checkout**: idempotent checkout, preview, shipping options, guest checkout, saved addresses,
  `expected_total` price-change protection.
- **flexcommerce-engagement**: wishlists, reviews with verified purchase and seller replies, product Q&A,
  recently viewed, price-drop alerts, trending searches.
- **flexcommerce-notifications**: an outbox with retries and deduplication, in-app inbox, device tokens,
  email, SMS (Twilio, Termii, Africa's Talking), FCM HTTP v1 push, default templates for every event, staff
  and vendor alerts.
- **flexcommerce-analytics**: sales, sales by state, payment methods, conversion, customers, dashboard KPIs,
  per-currency figures, scheduled aggregation.
- **flexcommerce-marketplace**: sellers, order splitting, commissions, vendor fulfilment and payouts.
- **flexcommerce**: a meta-package with `[all]`, `[marketplace]`, `[celery]` and `[fcm]` extras.

### Security
- Products are resolved only from the configured `PRODUCT_MODELS` / `ENGAGEMENT_MODELS` whitelist.
- Prices are computed on the server; payments are verified with the gateway, with amount and currency checks.
- Webhooks are signed with HMAC-SHA256 and a timestamp. They are protected against SSRF and do not follow
  redirects.
- Permissions are checked per object. Sensitive endpoints are throttled. CSV exports are protected against
  formula injection.

### Reliability under load
- Stock, coupon usage, flash-sale caps and wallet balances use conditional updates. Carts and orders use row
  locks.
- Checkout is idempotent.
- Events fire after commit, and a failing receiver cannot break a request.
- Tested on Python 3.10+ and Django 4.2, 5.2 and 6.0. The tests include concurrency checks on PostgreSQL.
