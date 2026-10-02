from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    "ORDER_NUMBER_PREFIX": "FC",
    "ORDER_NUMBER_GENERATOR": None,  # dotted path to a callable returning a unique string
    # Unpaid online / bank-transfer orders are cancelled (and stock released) after:
    "UNPAID_ORDER_TIMEOUT_MINUTES": 60,
    "BANK_TRANSFER_TIMEOUT_HOURS": 48,
    # Statuses in which a customer may cancel their own order.
    "CUSTOMER_CANCELLABLE_STATUSES": ["pending", "confirmed"],
    # Days after delivery during which a return can be requested (0 disables returns).
    "RETURN_WINDOW_DAYS": 7,
    # Process refunds created by cancellations immediately (otherwise staff approve them).
    "AUTO_PROCESS_CANCELLATION_REFUNDS": False,
}

register_defaults(DEFAULTS)


def orders_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
