from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    # How long running flash sales / automatic promotions are cached (seconds).
    "DISCOUNT_CACHE_SECONDS": 30,
    # Apply the best automatic promotion when the cart has no coupon code.
    "AUTO_APPLY_PROMOTIONS": True,
}

register_defaults(DEFAULTS)


def discounts_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
