from django.contrib import admin

from .models import AnalyticsEvent, DailyRevenueSummary


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_type",
        "reference",
        "amount",
        "currency",
        "user",
        "vendor_id",
        "created_at",
    )
    list_filter = ("event_type", "currency")
    search_fields = ("reference", "user__email")
    readonly_fields = [f.name for f in AnalyticsEvent._meta.concrete_fields]
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DailyRevenueSummary)
class DailyRevenueSummaryAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "currency",
        "order_count",
        "gross_revenue",
        "net_revenue",
        "tax_collected",
        "discount_given",
        "refund_total",
        "average_order_value",
        "new_customers",
    )
    list_filter = ("currency",)
    ordering = ("-date",)
    readonly_fields = [f.name for f in DailyRevenueSummary._meta.concrete_fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
