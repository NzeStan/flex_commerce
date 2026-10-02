from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    # Models customers can review / wishlist / view. None = catalog Product (if
    # installed) plus every PRODUCT_MODELS entry.
    "ENGAGEMENT_MODELS": None,
    "REVIEWS_REQUIRE_APPROVAL": True,
    "REVIEWS_VERIFIED_ONLY": False,  # only customers who bought the product may review
    "RECENTLY_VIEWED_LIMIT": 20,
    "RECENT_SEARCHES_LIMIT": 10,
    "QUESTIONS_REQUIRE_APPROVAL": True,
    "WISHLIST_PRICE_DROP_PERCENT": 5,  # alert when the price falls by at least this much
}

register_defaults(DEFAULTS)


def engagement_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
