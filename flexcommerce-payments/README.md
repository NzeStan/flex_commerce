# flexcommerce-payments

Payments for Nigeria and Africa: Paystack, Flutterwave, bank transfer, pay on delivery and a customer wallet — with verified webhooks and a pluggable gateway interface.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Every payment re-verified with the provider; amount and currency checked before an order is paid
- Signature-verified, deduplicated webhooks
- Refunds through the same gateway or to the wallet
- Wallet with an idempotent, never-negative ledger and top-ups
- Add Stripe, Monnify, Opay… by writing one class

## Install

```bash
pip install flexcommerce-payments
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_orders", "flexcommerce_payments"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `POST /api/payments/initiate/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
