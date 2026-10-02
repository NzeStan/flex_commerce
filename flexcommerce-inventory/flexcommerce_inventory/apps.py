from django.apps import AppConfig


class FlexcommerceInventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_inventory"
    verbose_name = "FlexCommerce Inventory"

    def ready(self):
        from flexcommerce_core.jobs import register_job

        from . import conf, signals  # noqa: F401

        register_job(
            "inventory.release_expired",
            "flexcommerce_inventory.services.release_expired",
            interval_minutes=5,
            description="Release stock held by unpaid orders whose reservation expired.",
        )
