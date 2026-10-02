from flexcommerce_core.conf import fc_setting, register_defaults

DEFAULTS = {
    # dotted path to f(queryset, query) -> queryset. Built-ins:
    #   flexcommerce_catalog.search.basic_search    (default, any database)
    #   flexcommerce_catalog.search.postgres_search (full-text + ranking)
    "CATALOG_SEARCH_HANDLER": "flexcommerce_catalog.search.basic_search",
    "CATALOG_CATEGORY_CACHE_SECONDS": 300,
    "CATALOG_MAX_CATEGORY_DEPTH": 8,
    "CATALOG_RELATED_LIMIT": 12,
}

register_defaults(DEFAULTS)


def catalog_setting(key):
    return fc_setting(key, DEFAULTS.get(key))
