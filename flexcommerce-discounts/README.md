# flexcommerce-discounts

Coupons, automatic promotions and flash sales — with usage limits that hold under heavy traffic.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Percentage (with cap), fixed, free shipping and buy-X-get-Y coupons
- Product, category, exclusion and vendor restrictions; minimum spend; first order only
- Global and per-customer (incl. guest email) limits enforced atomically at checkout
- Automatic code-less promotions (best one wins)
- Flash sales with per-product sale prices and "only N left" caps

## Install

```bash
pip install flexcommerce-discounts
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_discounts"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `POST /api/cart/coupon/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
