from rest_framework import serializers

from .models import Address
from .validators import validate_phone


class AddressInputSerializer(serializers.Serializer):
    """Validates an address dict (used by checkout for snapshots)."""

    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, validators=[validate_phone])
    alt_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, validators=[validate_phone])
    line1 = serializers.CharField(max_length=255)
    line2 = serializers.CharField(max_length=255, required=False, allow_blank=True)
    landmark = serializers.CharField(max_length=255, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100)
    lga = serializers.CharField(max_length=100, required=False, allow_blank=True)
    state = serializers.CharField(max_length=100)
    country = serializers.CharField(max_length=100, required=False, default="Nigeria")
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True)


class AddressSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Address
        fields = [
            "id",
            "address_type",
            "label",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "alt_phone",
            "line1",
            "line2",
            "landmark",
            "city",
            "lga",
            "state",
            "country",
            "postal_code",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "phone": {"validators": [validate_phone]},
            "alt_phone": {"validators": [validate_phone]},
        }
