from django.apps import AppConfig


class FlexcommercePaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_payments"
    verbose_name = "FlexCommerce Payments"

    def ready(self):
        from flexcommerce_core import hooks
        from flexcommerce_core.jobs import register_job

        from . import conf, signals  # noqa: F401
        from .services import PaymentService

        hooks.register("refund.process", PaymentService.refund_for_order)
        register_job(
            "payments.expire_stale",
            "flexcommerce_payments.services.expire_stale_payments",
            interval_minutes=60,
            description="Re-verify and abandon payments left pending for over 24 hours.",
        )
