from django.apps import AppConfig


class FlexcommerceEngagementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_engagement"
    verbose_name = "FlexCommerce Engagement"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from flexcommerce_core.jobs import register_job

        from . import conf, signals  # noqa: F401
        from .services import on_user_logged_in

        user_logged_in.connect(on_user_logged_in, dispatch_uid="flexcommerce_engagement_merge_history")
        register_job(
            "engagement.price_drops",
            "flexcommerce_engagement.services.detect_price_drops",
            interval_minutes=360,
            description="Alert customers when a wishlisted product gets cheaper.",
        )
