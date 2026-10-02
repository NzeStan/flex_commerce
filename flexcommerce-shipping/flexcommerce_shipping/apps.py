from django.apps import AppConfig


class FlexcommerceShippingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_shipping"
    verbose_name = "FlexCommerce Shipping"

    def ready(self):
        from . import conf  # noqa: F401
