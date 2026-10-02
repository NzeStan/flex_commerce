from rest_framework import serializers

from .models import Payment, Wallet, WalletTransaction


class PaymentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True, default=None)
    instructions = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "reference",
            "provider",
            "purpose",
            "order_number",
            "amount",
            "currency",
            "status",
            "channel",
            "authorization_url",
            "instructions",
            "failure_reason",
            "verified_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_instructions(self, obj):
        return (obj.gateway_response or {}).get("instructions") or None


class InitiatePaymentSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=50)
    order_id = serializers.UUIDField(required=False)
    order_number = serializers.CharField(max_length=50, required=False)
    token = serializers.CharField(max_length=64, required=False, help_text="Guest order access token")
    callback_url = serializers.URLField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("order_id") and not attrs.get("order_number"):
            raise serializers.ValidationError("Provide order_id or order_number.")
        return attrs


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["id", "balance", "currency", "is_active", "updated_at"]
        read_only_fields = fields


class WalletTransactionSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True, default=None)

    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "type",
            "source",
            "amount",
            "balance_after",
            "reference",
            "description",
            "order_number",
            "created_at",
        ]
        read_only_fields = fields


class TopUpSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=1)
    provider = serializers.CharField(max_length=50)
    callback_url = serializers.URLField(max_length=1000, required=False, allow_blank=True)


class WalletAdjustSerializer(serializers.Serializer):
    user_id = serializers.CharField(max_length=64)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    description = serializers.CharField(max_length=255)
    reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
