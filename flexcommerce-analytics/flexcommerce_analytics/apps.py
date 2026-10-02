from django.apps import AppConfig


class FlexcommerceAnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flexcommerce_analytics"
    verbose_name = "FlexCommerce Analytics"

    def ready(self):
        from flexcommerce_core.jobs import register_job

        from .signal_handlers import connect_all_signals

        connect_all_signals()
        register_job(
            "analytics.daily_summary",
            "flexcommerce_analytics.aggregation.aggregate_yesterday",
            interval_minutes=60,
            description="Rebuild daily revenue summaries for yesterday and today.",
        )
