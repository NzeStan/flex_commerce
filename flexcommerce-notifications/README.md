# flexcommerce-notifications

Signal-driven notifications on email, SMS, push and an in-app inbox — with an outbox, retries, deduplication, per-user preferences and admin-editable templates.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- Zero wiring: order, payment, shipping, refund, cart-recovery, stock, vendor and staff messages
- SMS: Termii, Africa's Talking, Twilio, Kudisms; push: FCM HTTP v1, OneSignal
- Default messages for every event; override per channel in the admin
- Per-event switches and channel routing in settings
- In-app inbox and device-token APIs

## Install

```bash
pip install flexcommerce-notifications
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core", "flexcommerce_notifications"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `GET /api/notifications/inbox/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
