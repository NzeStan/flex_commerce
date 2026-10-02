from django.apps import AppConfig


class FlexcommerceNotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_notifications"
    verbose_name = "FlexCommerce Notifications"

    def ready(self):
        from flexcommerce_core.jobs import register_job

        from . import conf  # noqa: F401
        from .signal_handlers import connect_all_signals

        connect_all_signals()
        register_job(
            "notifications.retry",
            "flexcommerce_notifications.dispatcher.deliver_due",
            interval_minutes=2,
            description="Deliver queued notifications and retry failed ones with backoff.",
        )
