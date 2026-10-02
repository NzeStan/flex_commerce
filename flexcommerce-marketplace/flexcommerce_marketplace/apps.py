from django.apps import AppConfig


class FlexcommerceMarketplaceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_marketplace"
    verbose_name = "FlexCommerce Marketplace"

    def ready(self):
        from flexcommerce_core import hooks
        from flexcommerce_core.jobs import register_job
        from flexcommerce_core.utils.products import is_installed
        from flexcommerce_orders import signals as order_signals

        from . import conf, services, signals  # noqa: F401

        hooks.register("order.created", services.split_order, order=10)
        hooks.register("catalog.vendor_id_for_user", services.vendor_id_for_user)
        order_signals.order_confirmed.connect(services.on_order_confirmed, dispatch_uid="mkt_order_confirmed")
        order_signals.order_cancelled.connect(services.on_order_cancelled, dispatch_uid="mkt_order_cancelled")
        order_signals.order_delivered.connect(services.on_order_delivered, dispatch_uid="mkt_order_delivered")
        order_signals.shipment_created.connect(services.on_shipment_created, dispatch_uid="mkt_shipment_created")
        order_signals.refund_processed.connect(services.allocate_refund, dispatch_uid="mkt_refund_processed")
        if is_installed("flexcommerce_engagement"):
            from flexcommerce_engagement.signals import rating_changed

            rating_changed.connect(services.on_rating_changed, dispatch_uid="mkt_rating_changed")
        register_job(
            "marketplace.payouts",
            "flexcommerce_marketplace.services.generate_payouts",
            interval_minutes=24 * 60,
            description="Create vendor payouts for earnings past the hold period.",
        )
