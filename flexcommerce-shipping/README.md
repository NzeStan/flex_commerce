# flexcommerce-shipping

Shipping for Nigeria out of the box (36 states + FCT in 8 zones) and configurable for anywhere: flat, per-item and weight-tier rates, free-shipping thresholds, pickup stations and delivery estimates.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- `seed_nigeria_shipping --with-methods` sets everything up
- Custom zones by state list or country
- Pickup stations with their own fees
- Pay-on-delivery availability per method
- Delivery date estimates that skip Sundays

## Install

```bash
pip install flexcommerce-shipping
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_shipping"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `POST /api/shipping/methods/calculate/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
