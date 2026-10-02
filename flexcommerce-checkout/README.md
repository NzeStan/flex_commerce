# flexcommerce-checkout

One atomic, idempotent checkout: re-prices the cart, validates delivery for the address, reserves stock, redeems the coupon, creates the order and starts the payment — safe against double submits and races.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Idempotency keys: retries return the original order
- `expected_total` guard against silent price changes
- Guest checkout, saved addresses, pickup stations, wallet payments
- Preview and shipping-options endpoints for checkout pages
- Works with only the cart and orders apps installed; uses shipping, inventory, discounts and payments when present

## Install

```bash
pip install flexcommerce-checkout
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_cart", "flexcommerce_orders", "flexcommerce_checkout"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `POST /api/checkout/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
