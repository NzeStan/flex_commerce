from rest_framework import serializers


class CheckoutSerializer(serializers.Serializer):
    payment_method = serializers.CharField(max_length=50)
    shipping_address = serializers.DictField(required=False)
    shipping_address_id = serializers.UUIDField(required=False)
    billing_address = serializers.DictField(required=False)
    shipping_method_id = serializers.UUIDField(required=False, allow_null=True)
    pickup_station_id = serializers.UUIDField(required=False, allow_null=True)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=100)
    customer_note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    callback_url = serializers.URLField(required=False, allow_blank=True, max_length=1000)
    expected_total = serializers.DecimalField(required=False, max_digits=14, decimal_places=2)
    save_address = serializers.BooleanField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if not attrs.get("shipping_address") and not attrs.get("shipping_address_id"):
            raise serializers.ValidationError({"shipping_address": "This field is required."})
        return attrs


class PreviewSerializer(serializers.Serializer):
    shipping_address = serializers.DictField(required=False)
    shipping_address_id = serializers.UUIDField(required=False)
    shipping_method_id = serializers.UUIDField(required=False, allow_null=True)
    pickup_station_id = serializers.UUIDField(required=False, allow_null=True)


class ShippingOptionsSerializer(serializers.Serializer):
    state = serializers.CharField(max_length=100, required=False, allow_blank=True)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True)
    shipping_address_id = serializers.UUIDField(required=False)


class AddressSerializer(serializers.Serializer):
    """Plain address serializer (standalone address validation)."""

    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    line1 = serializers.CharField(max_length=255)
    line2 = serializers.CharField(max_length=255, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=100)
    country = serializers.CharField(max_length=100, default="Nigeria")
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
