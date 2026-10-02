from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    # Products with no InventoryItem: False = unlimited (untracked), True = out of stock.
    "INVENTORY_TRACK_BY_DEFAULT": False,
    # How long checkout holds stock for an unpaid order (minutes). 0 = forever.
    "STOCK_RESERVATION_MINUTES": 60,
}

register_defaults(DEFAULTS)


def inventory_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
