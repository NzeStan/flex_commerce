"""
FlexCommerce settings loader.

Every FlexCommerce package registers its own defaults with ``register_defaults``.
Projects override any of them through a single dict in Django settings::

    FLEXCOMMERCE = {"CURRENCY": "NGN", "VAT_RATE": 0.075, ...}

Values are cached and the cache is cleared automatically when the
``FLEXCOMMERCE`` setting changes (e.g. ``override_settings`` in tests).
"""

from django.conf import settings
from django.core.signals import setting_changed

GLOBAL_DEFAULTS = {
    # ── Catalogue ────────────────────────────────────────────────────────────
    # Dotted "app_label.Model" paths of purchasable models. ``None`` means
    # "use flexcommerce_catalog.ProductVariant when the catalog app is installed".
    "PRODUCT_MODELS": None,
    # ── Money & tax ──────────────────────────────────────────────────────────
    "CURRENCY": "NGN",
    "CURRENCY_SYMBOL": "₦",
    "DECIMAL_PLACES": 2,
    "VAT_RATE": 0.075,  # 7.5% — Nigeria
    "VAT_INCLUSIVE": False,  # True when catalogue prices already include VAT
    "VAT_ON_SHIPPING": False,
    # ── Platform ─────────────────────────────────────────────────────────────
    "MARKETPLACE_MODE": False,
    # "sync" | "threading" | "celery" | dotted path to a callable(path, args, kwargs)
    "ASYNC_EXECUTOR": "sync",
    "AUDIT_ENABLED": True,
    "SOFT_DELETE": True,
    "HOOKS": {},  # {"order.created": ["myapp.hooks.fn", ...]}
    # ── API ──────────────────────────────────────────────────────────────────
    "PAGE_SIZE": 20,
    "MAX_PAGE_SIZE": 100,
    "THROTTLING_ENABLED": True,
    "THROTTLE_RATES": {},  # merged over DEFAULT_THROTTLE_RATES below
    # ── Outgoing webhooks ────────────────────────────────────────────────────
    "WEBHOOK_TIMEOUT": 10,
    "WEBHOOK_MAX_ATTEMPTS": 8,
    "WEBHOOK_DISABLE_AFTER_FAILURES": 50,
    "WEBHOOK_ALLOW_PRIVATE_URLS": False,  # SSRF protection; enable only for local dev
    "WEBHOOK_REQUIRE_HTTPS": True,
    # ── Legacy / shared (kept for backwards compatibility) ───────────────────
    "LOW_STOCK_THRESHOLD": 5,
    "ALLOW_OVERSELL": False,
}

DEFAULT_THROTTLE_RATES = {
    "checkout": "30/hour",
    "coupon": "30/minute",
    "review": "30/hour",
    "payment": "60/hour",
    "guest_order_lookup": "20/hour",
    "vendor_signup": "5/hour",
    "stock_alert": "30/hour",
}

_registered_defaults = {}
_cache = None


def register_defaults(defaults: dict) -> None:
    """Register package-level defaults. Safe to call more than once."""
    global _cache
    _registered_defaults.update(defaults)
    _cache = None


def get_flexcommerce_settings() -> dict:
    global _cache
    if _cache is None:
        user_settings = getattr(settings, "FLEXCOMMERCE", None) or {}
        _cache = {**GLOBAL_DEFAULTS, **_registered_defaults, **user_settings}
    return _cache


def fc_setting(key, default=None):
    """Return a single FlexCommerce setting (``default`` if unknown)."""
    merged = get_flexcommerce_settings()
    if key in merged:
        return merged[key]
    return default


def throttle_rate(scope: str):
    rates = {**DEFAULT_THROTTLE_RATES, **(fc_setting("THROTTLE_RATES") or {})}
    return rates.get(scope)


def _reload(*, setting, **kwargs):
    global _cache
    if setting == "FLEXCOMMERCE":
        _cache = None


setting_changed.connect(_reload)
