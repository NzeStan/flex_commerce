from rest_framework import serializers

from .models import PickupStation, ShippingMethod, ShippingZone


class ShippingZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingZone
        fields = ["id", "name", "zone_code", "states", "countries", "is_active", "sort_order"]


class ShippingMethodSerializer(serializers.ModelSerializer):
    zones = serializers.PrimaryKeyRelatedField(queryset=ShippingZone.objects.all(), many=True, required=False)
    estimated_delivery = serializers.SerializerMethodField()

    class Meta:
        model = ShippingMethod
        fields = [
            "id",
            "name",
            "code",
            "carrier",
            "description",
            "zones",
            "rate_type",
            "base_rate",
            "per_item_rate",
            "weight_tiers",
            "per_kg_rate",
            "max_weight",
            "free_shipping_threshold",
            "is_pickup",
            "pay_on_delivery",
            "estimated_days_min",
            "estimated_days_max",
            "estimated_delivery",
            "apply_vat",
            "is_active",
            "sort_order",
        ]

    def get_estimated_delivery(self, obj):
        if obj.estimated_days_min == obj.estimated_days_max:
            return f"{obj.estimated_days_min} day(s)"
        return f"{obj.estimated_days_min}–{obj.estimated_days_max} days"

    def validate(self, attrs):
        instance = ShippingMethod(**{k: v for k, v in attrs.items() if k != "zones"})
        if self.instance is not None:
            for field in ("weight_tiers", "estimated_days_min", "estimated_days_max"):
                if field not in attrs:
                    setattr(instance, field, getattr(self.instance, field))
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs


class PickupStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PickupStation
        fields = [
            "id",
            "name",
            "code",
            "zone",
            "state",
            "city",
            "address",
            "landmark",
            "phone",
            "opening_hours",
            "fee",
            "latitude",
            "longitude",
            "is_active",
        ]


class ShippingCostSerializer(serializers.Serializer):
    state = serializers.CharField(required=False, allow_blank=True, max_length=100)
    country = serializers.CharField(required=False, allow_blank=True, max_length=100)
    cart_total = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, default=0, min_value=0)
    item_count = serializers.IntegerField(required=False, default=1, min_value=0, max_value=100000)
    weight = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, default=0, min_value=0)
