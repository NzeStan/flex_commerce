from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    "MARKETPLACE_COMMISSION_RATE": 0.10,
    "VENDOR_AUTO_APPROVE": False,
    # Days after delivery before earnings can be paid out. None = RETURN_WINDOW_DAYS.
    "VENDOR_PAYOUT_HOLD_DAYS": None,
    "VENDOR_MINIMUM_PAYOUT": 1000,
}

register_defaults(DEFAULTS)


def marketplace_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
