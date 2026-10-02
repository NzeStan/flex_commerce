from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    "GUEST_CHECKOUT": True,
    # With flexcommerce_shipping installed, require a shipping method at checkout.
    "CHECKOUT_REQUIRE_SHIPPING": True,
    # Gateway used when a client sends the generic "card" payment method.
    "DEFAULT_CARD_GATEWAY": "paystack",
    # Save new addresses to the signed-in customer's address book.
    "CHECKOUT_SAVE_ADDRESSES": True,
    # Seconds a request-level idempotency key is cached (keys are also stored on orders).
    "CHECKOUT_IDEMPOTENCY_TTL": 300,
}

register_defaults(DEFAULTS)


def checkout_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
