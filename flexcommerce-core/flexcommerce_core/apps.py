from django.apps import AppConfig


class FlexcommerceCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_core"
    verbose_name = "FlexCommerce Core"

    def ready(self):
        from . import (
            checks,  # noqa: F401  (registers system checks)
            signals,
            webhooks,
        )
        from .jobs import register_job
        from .utils import pricing  # noqa: F401  (registers default price/tax handlers)

        signals.event.connect(webhooks.on_domain_event, dispatch_uid="flexcommerce_webhooks")
        register_job(
            "webhooks.retry",
            "flexcommerce_core.webhooks.deliver_due",
            interval_minutes=1,
            description="Retry pending outgoing webhook deliveries.",
        )
