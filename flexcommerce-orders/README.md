# flexcommerce-orders

The full order lifecycle: payment, confirmation, partial shipments with tracking events, delivery, returns within a return window, refunds, guest order tracking and a customer-visible timeline.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Every state change goes through one service with row locking and side effects (stock, coupons)
- Unpaid orders expire and release stock automatically
- Refunds validated against what was paid, paid out via the original gateway or wallet
- Returns with reasons, photos, approval, receipt (restock) and refund
- Guest tracking by order number + email, or a secret link
- `export_orders` CSV command

## Install

```bash
pip install flexcommerce-orders
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_orders"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `POST /api/orders/{id}/return/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
