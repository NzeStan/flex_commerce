from django.apps import AppConfig


class FlexcommerceCheckoutConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_checkout"
    verbose_name = "FlexCommerce Checkout"

    def ready(self):
        from . import conf  # noqa: F401
