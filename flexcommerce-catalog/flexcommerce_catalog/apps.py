from django.apps import AppConfig


class FlexcommerceCatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_catalog"
    verbose_name = "FlexCommerce Catalog"

    def ready(self):
        from . import (
            conf,  # noqa: F401  (registers defaults)
            receivers,
        )

        receivers.connect()
