from django.apps import AppConfig


class FlexcommerceDiscountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_discounts"
    verbose_name = "FlexCommerce Discounts"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from flexcommerce_core import hooks

        from . import (
            conf,  # noqa: F401
            services,
        )
        from .models import Coupon, FlashSale, FlashSaleItem

        hooks.register("price.modify", services.flash_sale_price, order=100)
        hooks.register("order.created", services.claim_flash_quantities)
        hooks.register("order.cancelled", services.release_flash_quantities)
        for model in (Coupon, FlashSale, FlashSaleItem):
            post_save.connect(
                services.invalidate_caches,
                sender=model,
                dispatch_uid=f"disc_inv_save_{model.__name__}",
            )
            post_delete.connect(
                services.invalidate_caches,
                sender=model,
                dispatch_uid=f"disc_inv_del_{model.__name__}",
            )
