"""
FlexCommerce — a complete, modular e-commerce and marketplace backend for Django.

``pip install "flexcommerce[all]"`` installs every app. Enable only what you need::

    from flexcommerce import FLEXCOMMERCE_APPS

    INSTALLED_APPS = [..., "rest_framework", *FLEXCOMMERCE_APPS]

Each entry can also be listed individually; every app except core is optional.
"""

__version__ = "1.0.0"

#: Every FlexCommerce app, in dependency order.
FLEXCOMMERCE_APPS = [
    "flexcommerce_core",
    "flexcommerce_catalog",
    "flexcommerce_pricing",
    "flexcommerce_inventory",
    "flexcommerce_discounts",
    "flexcommerce_shipping",
    "flexcommerce_cart",
    "flexcommerce_orders",
    "flexcommerce_payments",
    "flexcommerce_checkout",
    "flexcommerce_engagement",
    "flexcommerce_notifications",
    "flexcommerce_analytics",
]

#: Add this for multi-vendor marketplaces (requires ``flexcommerce[marketplace]``).
MARKETPLACE_APPS = ["flexcommerce_marketplace"]
