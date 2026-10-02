from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    "NOTIFICATION_CHANNELS": ["email", "sms", "push", "in_app"],
    "NOTIFICATION_EMAIL_BACKEND": "flexcommerce_notifications.backends.email.DjangoEmailBackend",
    "NOTIFICATION_SMS_BACKEND": "flexcommerce_notifications.backends.NullBackend",
    "NOTIFICATION_PUSH_BACKEND": "flexcommerce_notifications.backends.NullBackend",
    "NOTIFICATION_IN_APP_BACKEND": "flexcommerce_notifications.backends.inapp.InAppBackend",
    # Per-event switches / channel overrides, e.g. {"cart.abandoned": False,
    # "order.shipped": ["email", "sms"]}
    "NOTIFICATION_EVENTS": {},
    "NOTIFICATION_MAX_ATTEMPTS": 5,
    # Who receives staff alerts (new order, low stock, review to moderate...).
    # Empty = every active staff user with an email address.
    "STAFF_NOTIFICATION_EMAILS": [],
    "STAFF_NOTIFICATION_EVENTS": [
        "staff.order_created",
        "inventory.low_stock",
        "inventory.out_of_stock",
        "review.submitted",
        "return.requested",
        "vendor.applied",
    ],
    # Used to build links in messages, e.g. "https://shop.example.com"
    "FRONTEND_URL": "",
    "ORDER_URL_TEMPLATE": "{frontend}/orders/{order_number}",
    "STORE_NAME": "Our store",
}

register_defaults(DEFAULTS)


def notifications_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
