from rest_framework import serializers

from .models import Order, OrderEvent, OrderItem, Refund, ReturnRequest, Shipment, ShipmentEvent


class OrderItemSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="object_id", read_only=True)
    paid_line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product_id",
            "parent_product_id",
            "product_name",
            "product_sku",
            "product_data",
            "quantity",
            "unit_price_net",
            "unit_vat",
            "unit_price_gross",
            "vat_rate",
            "line_total",
            "line_vat",
            "discount_amount",
            "paid_line_total",
            "quantity_shipped",
            "quantity_returned",
            "vendor_id",
        ]
        read_only_fields = fields


class ShipmentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentEvent
        fields = ["id", "status", "description", "location", "occurred_at"]


class ShipmentSerializer(serializers.ModelSerializer):
    events = ShipmentEventSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = [
            "id",
            "carrier",
            "tracking_number",
            "tracking_url",
            "status",
            "shipped_at",
            "delivered_at",
            "estimated_delivery",
            "items",
            "vendor_id",
            "events",
        ]


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = [
            "id",
            "amount",
            "reason",
            "method",
            "status",
            "processed_at",
            "reference",
            "failure_reason",
            "items",
            "return_request",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "processed_at",
            "reference",
            "failure_reason",
            "return_request",
            "created_at",
        ]


class ReturnRequestSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)

    class Meta:
        model = ReturnRequest
        fields = [
            "id",
            "order",
            "order_number",
            "reason_code",
            "reason",
            "status",
            "items",
            "image_urls",
            "refund_method",
            "staff_note",
            "received_at",
            "created_at",
        ]
        read_only_fields = fields


class OrderEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderEvent
        fields = ["id", "event", "message", "created_at"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    shipments = ShipmentSerializer(many=True, read_only=True)
    refunds = RefundSerializer(many=True, read_only=True)
    timeline = serializers.SerializerMethodField()
    balance_due = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    can_cancel = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "status",
            "payment_status",
            "payment_method",
            "payment_provider",
            "email",
            "phone",
            "shipping_address",
            "billing_address",
            "subtotal",
            "discount_amount",
            "shipping_cost",
            "shipping_vat",
            "tax_total",
            "grand_total",
            "amount_paid",
            "amount_refunded",
            "balance_due",
            "currency",
            "coupon_code",
            "shipping_method_name",
            "pickup_station",
            "estimated_delivery_from",
            "estimated_delivery_to",
            "customer_note",
            "cancellation_reason",
            "payment_due_at",
            "confirmed_at",
            "paid_at",
            "shipped_at",
            "delivered_at",
            "cancelled_at",
            "can_cancel",
            "items",
            "shipments",
            "refunds",
            "timeline",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_timeline(self, obj):
        events = [e for e in obj.events.all() if e.is_customer_visible]
        return OrderEventSerializer(events, many=True).data

    def get_can_cancel(self, obj):
        from .conf import orders_setting

        return obj.status in orders_setting("CUSTOMER_CANCELLABLE_STATUSES")


class StaffOrderSerializer(OrderSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    all_events = serializers.SerializerMethodField()

    class Meta(OrderSerializer.Meta):
        fields = OrderSerializer.Meta.fields + [
            "user",
            "internal_note",
            "payment_reference",
            "all_events",
        ]

    def get_all_events(self, obj):
        return [
            {
                "event": e.event,
                "message": e.message,
                "actor": e.actor,
                "visible": e.is_customer_visible,
                "data": e.data,
                "created_at": e.created_at,
            }
            for e in obj.events.all()
        ]


class OrderListSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "status",
            "payment_status",
            "payment_method",
            "grand_total",
            "currency",
            "item_count",
            "created_at",
        ]

    def get_item_count(self, obj):
        return sum(i.quantity for i in obj.items.all())


class OrderTransitionSerializer(serializers.Serializer):
    to_state = serializers.ChoiceField(choices=Order.STATUS_CHOICES)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ItemQuantitySerializer(serializers.Serializer):
    order_item_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)


class CreateShipmentSerializer(serializers.Serializer):
    carrier = serializers.CharField(max_length=100, required=False, allow_blank=True)
    tracking_number = serializers.CharField(max_length=200, required=False, allow_blank=True)
    tracking_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    estimated_delivery = serializers.DateField(required=False, allow_null=True)
    items = ItemQuantitySerializer(many=True, required=False)


class ShipmentUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Shipment.STATUS_CHOICES)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)


class CreateRefundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    reason = serializers.CharField(max_length=2000)
    method = serializers.ChoiceField(choices=Refund.METHOD_CHOICES, default=Refund.METHOD_ORIGINAL)
    items = ItemQuantitySerializer(many=True, required=False)
    process = serializers.BooleanField(default=False)


class ReturnCreateSerializer(serializers.Serializer):
    items = ItemQuantitySerializer(many=True, allow_empty=False)
    reason_code = serializers.ChoiceField(choices=ReturnRequest.REASON_CHOICES, default="other")
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    image_urls = serializers.ListField(child=serializers.URLField(max_length=500), required=False, max_length=10)
    refund_method = serializers.ChoiceField(choices=Refund.METHOD_CHOICES, default=Refund.METHOD_ORIGINAL)


class ReturnDecisionSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    restock = serializers.BooleanField(default=True)
    refund = serializers.BooleanField(default=True)


class MarkPaidSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    reference = serializers.CharField(max_length=200, required=False, allow_blank=True)


class GuestLookupSerializer(serializers.Serializer):
    order_number = serializers.CharField(max_length=50)
    email = serializers.EmailField()
