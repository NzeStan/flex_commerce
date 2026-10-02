from rest_framework import serializers

from flexcommerce_core.utils.products import product_type_label

from .models import InventoryItem, StockAlert, StockMovement, StockReservation


class InventoryItemSerializer(serializers.ModelSerializer):
    available = serializers.IntegerField(read_only=True)
    is_low = serializers.BooleanField(read_only=True)
    product_type = serializers.SerializerMethodField()
    product_id = serializers.UUIDField(source="object_id", read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "product_type",
            "product_id",
            "sku",
            "on_hand",
            "reserved",
            "sold",
            "available",
            "reorder_point",
            "is_low",
            "allow_oversell",
            "updated_at",
        ]
        read_only_fields = ["id", "on_hand", "reserved", "sold", "updated_at"]

    def get_product_type(self, obj):
        model = obj.content_type.model_class()
        return product_type_label(model) if model else None


class InventoryCreateSerializer(serializers.Serializer):
    product_type = serializers.CharField(required=False, allow_blank=True)
    product_id = serializers.UUIDField()
    on_hand = serializers.IntegerField(min_value=0, default=0)
    reorder_point = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    allow_oversell = serializers.BooleanField(default=False)
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True)


class QuantitySerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=10_000_000)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=100)


class AdjustSerializer(serializers.Serializer):
    on_hand = serializers.IntegerField(min_value=0, max_value=10_000_000)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class StockMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockMovement
        fields = ["id", "movement_type", "quantity", "note", "reference", "created_at"]


class StockReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockReservation
        fields = [
            "id",
            "inventory_item",
            "quantity",
            "order_ref",
            "status",
            "expires_at",
            "created_at",
        ]


class StockAlertSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(write_only=True, required=False, allow_blank=True)
    product_id = serializers.UUIDField(source="object_id")

    class Meta:
        model = StockAlert
        fields = ["id", "product_type", "product_id", "email", "phone", "notified_at", "created_at"]
        read_only_fields = ["id", "notified_at", "created_at"]
