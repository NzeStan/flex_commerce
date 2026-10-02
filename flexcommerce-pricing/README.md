# flexcommerce-pricing

Tax categories (standard / zero-rated / exempt / custom rate), a VAT calculator and authoritative price quotes that include flash sales and tax.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Admin-managed tax categories used by the price pipeline
- `POST /pricing/calculate/` VAT breakdown, inclusive or exclusive
- `POST /pricing/quote/` server-side prices for any products

## Install

```bash
pip install flexcommerce-pricing
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_pricing"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `POST /api/pricing/quote/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
