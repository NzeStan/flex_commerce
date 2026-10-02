from rest_framework import serializers

from .models import AnalyticsEvent, DailyRevenueSummary


class AnalyticsEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalyticsEvent
        fields = [
            "id",
            "event_type",
            "user",
            "amount",
            "currency",
            "reference",
            "vendor_id",
            "meta",
            "created_at",
        ]
        read_only_fields = fields


class DailyRevenueSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyRevenueSummary
        fields = [
            "id",
            "date",
            "currency",
            "order_count",
            "units_sold",
            "gross_revenue",
            "net_revenue",
            "tax_collected",
            "shipping_collected",
            "discount_given",
            "refund_total",
            "average_order_value",
            "cancelled_order_count",
            "abandoned_cart_count",
            "new_customers",
        ]
        read_only_fields = fields
