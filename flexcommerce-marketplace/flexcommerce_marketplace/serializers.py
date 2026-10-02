from rest_framework import serializers

from .models import Payout, Vendor, VendorOrder


class PublicVendorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "logo_url",
            "banner_url",
            "is_official_store",
            "rating_avg",
            "rating_count",
            "created_at",
        ]
        read_only_fields = fields


class VendorApplySerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = [
            "name",
            "description",
            "logo_url",
            "banner_url",
            "email",
            "phone",
            "address",
            "business_registration_number",
            "tax_id",
            "kyc_documents",
            "bank_name",
            "bank_code",
            "account_number",
            "account_name",
        ]

    def validate_account_number(self, value):
        if value and (not value.isdigit() or len(value) != 10):
            raise serializers.ValidationError("Enter a 10-digit NUBAN account number.")
        return value


class VendorProfileSerializer(VendorApplySerializer):
    class Meta(VendorApplySerializer.Meta):
        fields = [
            "id",
            "slug",
            "status",
            "status_reason",
            "commission_rate",
            "is_official_store",
            "rating_avg",
            "rating_count",
            "approved_at",
        ] + VendorApplySerializer.Meta.fields
        read_only_fields = [
            "id",
            "slug",
            "status",
            "status_reason",
            "commission_rate",
            "is_official_store",
            "rating_avg",
            "rating_count",
            "approved_at",
            "name",
        ]


class StaffVendorSerializer(VendorProfileSerializer):
    class Meta(VendorProfileSerializer.Meta):
        fields = VendorProfileSerializer.Meta.fields + ["owner"]
        read_only_fields = [
            "id",
            "slug",
            "status",
            "status_reason",
            "rating_avg",
            "rating_count",
            "approved_at",
            "owner",
        ]


class VendorOrderSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    items = serializers.SerializerMethodField()
    shipping_address = serializers.JSONField(source="order.shipping_address", read_only=True)
    customer_note = serializers.CharField(source="order.customer_note", read_only=True)

    class Meta:
        model = VendorOrder
        fields = [
            "id",
            "order_number",
            "status",
            "gross_amount",
            "commission_rate",
            "commission_amount",
            "refunded_amount",
            "net_amount",
            "delivered_at",
            "available_at",
            "payout",
            "items",
            "shipping_address",
            "customer_note",
            "created_at",
        ]
        read_only_fields = fields

    def get_items(self, obj):
        return [
            {
                "order_item_id": str(i.pk),
                "name": i.product_name,
                "sku": i.product_sku,
                "quantity": i.quantity,
                "quantity_shipped": i.quantity_shipped,
                "unit_price": str(i.unit_price_gross),
                "line_total": str(i.paid_line_total),
            }
            for i in obj.order.items.all()
            if i.vendor_id == obj.vendor_id
        ]


class ShipSerializer(serializers.Serializer):
    carrier = serializers.CharField(max_length=100, required=False, allow_blank=True)
    tracking_number = serializers.CharField(max_length=200, required=False, allow_blank=True)
    tracking_url = serializers.URLField(max_length=500, required=False, allow_blank=True)


class PayoutSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    order_count = serializers.SerializerMethodField()

    class Meta:
        model = Payout
        fields = [
            "id",
            "vendor",
            "vendor_name",
            "amount",
            "currency",
            "status",
            "reference",
            "failure_reason",
            "processed_at",
            "order_count",
            "created_at",
        ]
        read_only_fields = fields

    def get_order_count(self, obj):
        return obj.vendor_orders.count()


class PayoutDecisionSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=200, required=False, allow_blank=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class StatusReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)
