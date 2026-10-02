from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    "CART_SESSION_KEY": "flexcommerce_cart",
    "CART_TOKEN_HEADER": "X-Cart-Token",
    "CART_EXPIRY_DAYS": 30,  # anonymous carts
    "CART_PURGE_DAYS": 90,  # delete expired/merged anonymous carts after this many days
    "CART_ABANDONMENT_HOURS": 1,
    "CART_MAX_QUANTITY_PER_ITEM": 100,
    "CART_MAX_ITEMS": 100,  # distinct lines
    "CART_MERGE_ON_LOGIN": True,
}

register_defaults(DEFAULTS)


def cart_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
