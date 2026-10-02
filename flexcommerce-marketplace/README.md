# flexcommerce-marketplace

Turn a store into a Jumia/Konga-style marketplace: seller onboarding, per-vendor order splitting, commissions, vendor fulfilment, refund charge-backs and payouts after the return window.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Vendor applications with KYC and bank details; staff approval
- Orders split per vendor inside the checkout transaction, commission snapshotted
- Vendors manage their own catalog products and ship their own items
- Refunds charged back to the right vendor
- Payouts generated once the hold period passes

## Install

```bash
pip install flexcommerce-marketplace
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_orders", "flexcommerce_marketplace"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `POST /api/vendors/me/orders/{id}/ship/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
