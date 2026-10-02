from rest_framework import serializers

from flexcommerce_core.utils.products import product_type_label

from .models import Coupon, CouponUsage, FlashSale, FlashSaleItem


class CouponSerializer(serializers.ModelSerializer):
    is_valid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Coupon
        fields = [
            "id",
            "code",
            "name",
            "description",
            "coupon_type",
            "value",
            "max_discount",
            "buy_quantity",
            "get_quantity",
            "minimum_cart_value",
            "applies_to_all",
            "restricted_categories",
            "restricted_products",
            "excluded_products",
            "vendor_id",
            "first_order_only",
            "auto_apply",
            "usage_limit",
            "per_user_limit",
            "used_count",
            "starts_at",
            "expires_at",
            "is_active",
            "is_valid",
            "created_at",
        ]
        read_only_fields = ["id", "used_count", "is_valid", "created_at"]

    def validate_code(self, value):
        value = value.strip().upper()
        qs = Coupon.objects.filter(code=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A coupon with this code already exists.")
        return value

    def validate(self, attrs):
        instance = Coupon(**{**(self._current() or {}), **attrs})
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "error_dict") else exc.messages)
        return attrs

    def _current(self):
        if self.instance is None:
            return None
        return {f.name: getattr(self.instance, f.name) for f in Coupon._meta.concrete_fields if f.name != "id"}


class CouponUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = CouponUsage
        fields = ["id", "user", "email", "order_ref", "discount_applied", "created_at"]


class CouponValidateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=100)
    cart_total = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)


class FlashSaleItemSerializer(serializers.ModelSerializer):
    product_type = serializers.SerializerMethodField()
    product_id = serializers.UUIDField(source="object_id", read_only=True)
    remaining = serializers.IntegerField(read_only=True)

    class Meta:
        model = FlashSaleItem
        fields = [
            "id",
            "product_type",
            "product_id",
            "sale_price",
            "quantity_limit",
            "sold_quantity",
            "remaining",
        ]

    def get_product_type(self, obj):
        model = obj.content_type.model_class()
        return product_type_label(model) if model else None


class FlashSaleItemWriteSerializer(serializers.Serializer):
    product_type = serializers.CharField(required=False, allow_blank=True)
    product_id = serializers.UUIDField()
    sale_price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True)
    quantity_limit = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class FlashSaleSerializer(serializers.ModelSerializer):
    is_running = serializers.BooleanField(read_only=True)
    items = FlashSaleItemSerializer(many=True, read_only=True)

    class Meta:
        model = FlashSale
        fields = [
            "id",
            "name",
            "discount_percentage",
            "starts_at",
            "ends_at",
            "is_active",
            "applicable_products",
            "is_running",
            "items",
        ]
        read_only_fields = ["id", "is_running", "items"]

    def validate(self, attrs):
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "Must be after the start."})
        pct = attrs.get("discount_percentage")
        if pct is not None and not (0 <= pct <= 100):
            raise serializers.ValidationError({"discount_percentage": "Must be between 0 and 100."})
        return attrs
