from decimal import Decimal

from rest_framework import serializers

from .models import PriceBreakdown, TaxCategory


class TaxCategorySerializer(serializers.ModelSerializer):
    effective_rate = serializers.DecimalField(source="get_rate", max_digits=5, decimal_places=4, read_only=True)

    class Meta:
        model = TaxCategory
        fields = ["id", "name", "code", "category_type", "rate", "effective_rate", "description"]
        read_only_fields = ["id", "effective_rate"]

    def validate_rate(self, value):
        if value is not None and not (0 <= value < 1):
            raise serializers.ValidationError("Rate must be a fraction between 0 and 1.")
        return value


class PriceBreakdownSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceBreakdown
        fields = [
            "id",
            "quantity",
            "unit_price_net",
            "unit_vat",
            "unit_price_gross",
            "vat_rate",
            "currency",
            "line_net",
            "line_vat",
            "line_gross",
        ]


class PriceCalculateSerializer(serializers.Serializer):
    price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    rate = serializers.DecimalField(
        max_digits=5,
        decimal_places=4,
        min_value=Decimal("0"),
        max_value=Decimal("0.9999"),
        required=False,
        allow_null=True,
    )
    inclusive = serializers.BooleanField(required=False, allow_null=True, default=None)


class QuoteItemSerializer(serializers.Serializer):
    product_type = serializers.CharField(required=False, allow_blank=True)
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=10000, default=1)


class QuoteSerializer(serializers.Serializer):
    items = QuoteItemSerializer(many=True, allow_empty=False, max_length=100)
