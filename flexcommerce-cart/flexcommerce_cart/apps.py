from django.apps import AppConfig


class FlexcommerceCartConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_cart"
    verbose_name = "FlexCommerce Cart"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from flexcommerce_core.jobs import register_job

        from . import conf, signals  # noqa: F401
        from .services import on_user_logged_in

        user_logged_in.connect(on_user_logged_in, dispatch_uid="flexcommerce_cart_merge_on_login")
        register_job(
            "cart.expire",
            "flexcommerce_cart.services.expire_carts",
            interval_minutes=60,
            description="Expire stale anonymous carts and purge old ones.",
        )
        register_job(
            "cart.abandoned",
            "flexcommerce_cart.services.notify_abandoned_carts",
            interval_minutes=15,
            description="Emit cart.abandoned for idle carts (drives recovery emails).",
        )
