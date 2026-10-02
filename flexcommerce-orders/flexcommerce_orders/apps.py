from django.apps import AppConfig


class FlexcommerceOrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_orders"
    verbose_name = "FlexCommerce Orders"

    def ready(self):
        from flexcommerce_core.jobs import register_job

        from . import conf, signals  # noqa: F401

        register_job(
            "orders.expire_unpaid",
            "flexcommerce_orders.services.expire_unpaid_orders",
            interval_minutes=5,
            description="Cancel orders whose payment deadline passed (releases stock and coupons).",
        )
