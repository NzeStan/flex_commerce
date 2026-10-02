from rest_framework import serializers

from flexcommerce_core.utils.products import (
    prefetch_products,
    product_name,
    product_sku,
    product_type_label,
)

from .models import Cart, CartItem, SavedItem


def display_map(rows):
    """``{(ct_id, object_id): {...}}`` product display info, batched per product model.

    A product model may implement ``bulk_cart_display(instances) -> {pk: dict}``
    (the catalog does: image, slug, attributes) to add fields cheaply.
    """
    products = prefetch_products(rows)
    by_model = {}
    for key, product in products.items():
        by_model.setdefault(type(product), []).append((key, product))
    result = {}
    for model, entries in by_model.items():
        extra = {}
        if hasattr(model, "bulk_cart_display"):
            extra = model.bulk_cart_display([p for _, p in entries])
        for key, product in entries:
            result[key] = {
                "type": product_type_label(product),
                "id": str(product.pk),
                "name": product_name(product),
                "sku": product_sku(product),
                **extra.get(product.pk, {}),
            }
    return result


class CartItemSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="object_id", read_only=True)
    product_type = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    line_total_with_tax = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    line_vat = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product_id",
            "product_type",
            "product",
            "quantity",
            "unit_price",
            "unit_price_with_tax",
            "unit_net",
            "vat_rate",
            "vat_amount",
            "discount_amount",
            "line_total",
            "line_total_with_tax",
            "line_vat",
            "created_at",
        ]
        read_only_fields = fields

    def _display(self, obj):
        return (self.context.get("display") or {}).get((obj.content_type_id, obj.object_id))

    def get_product(self, obj):
        return self._display(obj)

    def get_product_type(self, obj):
        info = self._display(obj)
        return info["type"] if info else None


class CartSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()
    subtotal = serializers.DecimalField(source="subtotal_amount", max_digits=14, decimal_places=2, read_only=True)
    tax_total = serializers.DecimalField(source="tax_amount", max_digits=14, decimal_places=2, read_only=True)
    total = serializers.DecimalField(source="total_amount", max_digits=14, decimal_places=2, read_only=True)
    item_count = serializers.IntegerField(source="items_count", read_only=True)
    cart_token = serializers.CharField(source="token", read_only=True)
    notices = serializers.SerializerMethodField()
    promotion = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = [
            "id",
            "cart_token",
            "status",
            "currency",
            "email",
            "phone",
            "coupon_code",
            "coupon_error",
            "promotion",
            "discount_amount",
            "free_shipping",
            "subtotal",
            "tax_total",
            "total",
            "item_count",
            "notices",
            "expires_at",
            "created_at",
            "updated_at",
            "items",
        ]
        read_only_fields = fields

    def get_items(self, cart):
        items = list(cart.items.all())
        context = {**self.context, "display": display_map(items)}
        return CartItemSerializer(items, many=True, context=context).data

    def get_notices(self, cart):
        return (cart.extra_data or {}).get("notices", [])

    def get_promotion(self, cart):
        return (cart.extra_data or {}).get("promotion")


EMPTY_CART = {
    "id": None,
    "cart_token": None,
    "status": "active",
    "coupon_code": "",
    "coupon_error": "",
    "promotion": None,
    "discount_amount": "0.00",
    "free_shipping": False,
    "subtotal": "0.00",
    "tax_total": "0.00",
    "total": "0.00",
    "item_count": 0,
    "notices": [],
    "items": [],
}


class AddItemSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    product_type = serializers.CharField(required=False, allow_blank=True, max_length=150)
    product_model = serializers.CharField(required=False, allow_blank=True, max_length=150, help_text="Alias")
    quantity = serializers.IntegerField(min_value=1, default=1)


class UpdateQuantitySerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0)


class ApplyCouponSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=100)


class ContactSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate_phone(self, value):
        from flexcommerce_core.validators import validate_phone

        validate_phone(value)
        return value


class SavedItemSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="object_id", read_only=True)
    product = serializers.SerializerMethodField()

    class Meta:
        model = SavedItem
        fields = ["id", "product_id", "product", "saved_price", "created_at"]
        read_only_fields = fields

    def get_product(self, obj):
        return (self.context.get("display") or {}).get((obj.content_type_id, obj.object_id))
