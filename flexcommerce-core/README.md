# flexcommerce-core

Foundation of FlexCommerce: settings, post-commit events, in-transaction hooks, background execution, maintenance jobs, signed outgoing webhooks, audit log, address book and the shared API conventions.

Part of **[FlexCommerce](https://github.com/NzeStan/flex_commerce/blob/main/README.md)** — a complete, modular e-commerce and marketplace backend for
Django + DRF, built for Nigeria and Africa. Every app is optional.

## Features

- One `FLEXCOMMERCE = {...}` settings dict; every package registers its defaults
- `events.emit()` — signals that fire only after the transaction commits, isolated from each other
- `hooks` — plugins that take part in the business operation (and can veto it)
- `tasks.enqueue()` — `sync`, `threading`, Celery or your own executor
- `flexcommerce_run_jobs` — one command for every package's maintenance jobs
- Outgoing webhooks: HMAC-SHA256 signatures, retries with backoff, SSRF protection
- Address book API (Nigeria-aware: LGA, landmark, phone validation)
- Consistent error format, pagination and throttling for every FlexCommerce API
- System checks that catch misconfiguration at `manage.py check`
- `pytest_plugins = ["flexcommerce_core.testing"]` helpers for your own tests

## Install

```bash
pip install flexcommerce-core
```

```python
INSTALLED_APPS = [..., "rest_framework", "flexcommerce_core"]
urlpatterns = [path("api/", include("flexcommerce_core.urls"))]
```

```bash
python manage.py migrate
```

Example: `GET/POST /api/addresses/`

## Documentation

- [Integration guide](https://github.com/NzeStan/flex_commerce/blob/main/INTEGRATION.md)
- [Settings](https://github.com/NzeStan/flex_commerce/blob/main/docs/settings.md) · [API](https://github.com/NzeStan/flex_commerce/blob/main/docs/api.md) · [Events & notifications](https://github.com/NzeStan/flex_commerce/blob/main/docs/events.md)
- [Extending](https://github.com/NzeStan/flex_commerce/blob/main/docs/extending.md) · [Operations](https://github.com/NzeStan/flex_commerce/blob/main/docs/operations.md) · [Changelog](https://github.com/NzeStan/flex_commerce/blob/main/CHANGELOG.md)

Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+. MIT licensed.
