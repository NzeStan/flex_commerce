"""Django system checks: catch misconfiguration at ``manage.py check`` / startup."""

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from .conf import fc_setting


@register(Tags.compatibility)
def check_flexcommerce_settings(app_configs, **kwargs):
    from .utils.products import get_product_model_paths, load_model
    from .utils.vat import to_decimal

    messages = []

    paths = get_product_model_paths()
    if not paths:
        messages.append(
            Warning(
                "FLEXCOMMERCE['PRODUCT_MODELS'] is empty.",
                hint="Install flexcommerce_catalog or set PRODUCT_MODELS to your product model(s), "
                "e.g. ['shop.Product'].",
                id="flexcommerce.W001",
            )
        )
    for path in paths:
        try:
            model = load_model(path)
        except (LookupError, ValueError) as exc:
            messages.append(Error(f"PRODUCT_MODELS entry '{path}' cannot be loaded: {exc}", id="flexcommerce.E001"))
            continue
        if model._meta.pk.get_internal_type() != "UUIDField":
            messages.append(
                Error(
                    f"Product model '{path}' must use a UUID primary key.",
                    hint="FlexCommerce stores product references as UUIDs.",
                    id="flexcommerce.E002",
                )
            )

    executor = fc_setting("ASYNC_EXECUTOR", "sync")
    if executor == "celery":
        try:
            import celery  # noqa: F401
        except ImportError:
            messages.append(
                Error(
                    "ASYNC_EXECUTOR is 'celery' but Celery is not installed.",
                    id="flexcommerce.E003",
                )
            )
    elif executor not in ("sync", "threading"):
        from django.utils.module_loading import import_string

        try:
            import_string(executor)
        except ImportError:
            messages.append(Error(f"ASYNC_EXECUTOR '{executor}' cannot be imported.", id="flexcommerce.E004"))
    elif executor == "sync" and not settings.DEBUG:
        messages.append(
            Warning(
                "ASYNC_EXECUTOR is 'sync': emails, SMS and webhooks are sent inside the request.",
                hint="Use 'celery' (or 'threading') in production so slow providers never slow checkout.",
                id="flexcommerce.W002",
            )
        )

    try:
        rate = to_decimal(fc_setting("VAT_RATE"))
        if not 0 <= rate < 1:
            raise ValueError
    except ValueError:
        messages.append(Error("VAT_RATE must be a fraction between 0 and 1 (e.g. 0.075).", id="flexcommerce.E005"))

    cache_backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if not settings.DEBUG and cache_backend.endswith(("LocMemCache", "DummyCache")):
        messages.append(
            Warning(
                "The default cache is process-local; throttling and job locks are not shared between workers.",
                hint="Use Redis or Memcached as the default cache in production.",
                id="flexcommerce.W003",
            )
        )
    return messages
