# flexcommerce-inventory

Concurrency-safe stock for any product model: atomic reservations that expire, sales, returns, adjustments, an audited movement log, CSV import and back-in-stock alerts.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Reservations via a single conditional UPDATE — the last unit can never be sold twice
- Reservations expire automatically (unpaid orders) and convert to sales on payment
- Low / out-of-stock / restocked signals that fire when the level crosses the threshold
- "Notify me when available" alerts for customers and guests
- `import_inventory stock.csv` and `reconcile_inventory` commands

## Install

```bash
pip install flexcommerce-inventory
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_inventory"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `POST /api/inventory/{id}/restock/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
