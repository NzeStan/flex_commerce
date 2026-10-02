from django.db import transaction
from rest_framework import serializers

from .models import Brand, Category, Product, ProductImage, ProductVariant


class CategorySerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), required=False, allow_null=True)

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "parent",
            "description",
            "image_url",
            "is_active",
            "sort_order",
            "depth",
            "meta_title",
            "meta_description",
        ]
        read_only_fields = ["id", "depth"]
        extra_kwargs = {"slug": {"required": False}}

    def validate(self, attrs):
        from .conf import catalog_setting

        parent = attrs.get("parent", getattr(self.instance, "parent", None))
        if parent is not None:
            if self.instance is not None and parent.path.startswith(self.instance.path):
                raise serializers.ValidationError({"parent": "A category cannot be moved under itself."})
            if parent.depth + 1 >= catalog_setting("CATALOG_MAX_CATEGORY_DEPTH"):
                raise serializers.ValidationError({"parent": "Category tree is too deep."})
        return attrs


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug", "description", "logo_url", "is_active", "is_official_store"]
        read_only_fields = ["id"]
        extra_kwargs = {"slug": {"required": False}}


class ProductImageSerializer(serializers.ModelSerializer):
    src = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["id", "product", "variant", "image", "url", "src", "alt_text", "is_primary", "sort_order"]
        read_only_fields = ["id", "src"]
        extra_kwargs = {"image": {"write_only": True, "required": False}, "product": {"required": False}}

    def get_src(self, obj):
        src = obj.get_url()
        request = self.context.get("request")
        if src and obj.image and request is not None:
            return request.build_absolute_uri(src)
        return src

    def validate(self, attrs):
        if not attrs.get("image") and not attrs.get("url") and self.instance is None:
            raise serializers.ValidationError("Provide an image file or an image URL.")
        return attrs


class VariantSerializer(serializers.ModelSerializer):
    discount_percent = serializers.IntegerField(read_only=True)
    in_stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "product",
            "sku",
            "name",
            "attributes",
            "price",
            "compare_at_price",
            "weight",
            "barcode",
            "is_active",
            "is_default",
            "sort_order",
            "discount_percent",
            "in_stock",
        ]
        read_only_fields = ["id", "discount_percent", "in_stock"]
        extra_kwargs = {"product": {"required": False}}

    def get_in_stock(self, obj):
        stock = self.context.get("stock_map")
        if stock is None:
            return None
        return stock.get(obj.pk, True)

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return value


class StaffVariantSerializer(VariantSerializer):
    class Meta(VariantSerializer.Meta):
        fields = VariantSerializer.Meta.fields + ["cost_price"]


class ProductListSerializer(serializers.ModelSerializer):
    brand = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    brand_name = serializers.CharField(source="brand.name", read_only=True, default=None)
    image = serializers.SerializerMethodField()
    discount_percent = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "short_description",
            "brand",
            "brand_name",
            "image",
            "min_price",
            "max_price",
            "compare_at_price",
            "discount_percent",
            "in_stock",
            "rating_avg",
            "rating_count",
            "sold_count",
            "is_featured",
            "vendor_id",
            "status",
        ]

    def get_image(self, obj):
        images = list(obj.images.all())
        if not images:
            return None
        return ProductImageSerializer(images[0], context=self.context).data["src"]


class ProductDetailSerializer(ProductListSerializer):
    categories = serializers.SerializerMethodField()
    variants = serializers.SerializerMethodField()
    images = ProductImageSerializer(many=True, read_only=True)
    breadcrumbs = serializers.SerializerMethodField()

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + [
            "description",
            "specifications",
            "tags",
            "categories",
            "variants",
            "images",
            "breadcrumbs",
            "vat_exempt",
            "meta_title",
            "meta_description",
            "published_at",
        ]

    def get_categories(self, obj):
        return [{"id": c.pk, "name": c.name, "slug": c.slug} for c in obj.categories.all()]

    def get_variants(self, obj):
        variants = [v for v in obj.variants.all() if v.is_active or self.context.get("show_inactive")]
        serializer = StaffVariantSerializer if self.context.get("show_cost") else VariantSerializer
        return serializer(variants, many=True, context=self.context).data

    def get_breadcrumbs(self, obj):
        categories = list(obj.categories.all())
        if not categories:
            return []
        deepest = max(categories, key=lambda c: c.depth)
        return [{"name": c.name, "slug": c.slug} for c in deepest.get_ancestors(include_self=True)]


class InlineVariantSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=100)
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    attributes = serializers.DictField(required=False)
    price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    compare_at_price = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    cost_price = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    weight = serializers.DecimalField(max_digits=10, decimal_places=3, required=False)
    barcode = serializers.CharField(max_length=64, required=False, allow_blank=True)
    is_default = serializers.BooleanField(required=False)

    def validate_sku(self, value):
        if ProductVariant.objects.filter(sku=value).exists():
            raise serializers.ValidationError("A variant with this SKU already exists.")
        return value


class ProductWriteSerializer(serializers.ModelSerializer):
    """
    Create/update a product. On create you may pass ``variants=[...]`` or, for a
    single-variant product, just ``price`` + ``sku`` at the top level.
    """

    brand = serializers.PrimaryKeyRelatedField(queryset=Brand.objects.all(), required=False, allow_null=True)
    categories = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), many=True, required=False)
    variants = InlineVariantSerializer(many=True, required=False, write_only=True)
    price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, write_only=True)
    compare_at_price = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True, write_only=True
    )
    sku = serializers.CharField(max_length=100, required=False, write_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "short_description",
            "description",
            "brand",
            "categories",
            "status",
            "is_featured",
            "specifications",
            "tags",
            "vat_exempt",
            "tax_category_code",
            "meta_title",
            "meta_description",
            "vendor_id",
            "variants",
            "price",
            "compare_at_price",
            "sku",
        ]
        read_only_fields = ["id", "vendor_id"]
        extra_kwargs = {"slug": {"required": False}}

    def validate(self, attrs):
        if self.instance is None and not attrs.get("variants"):
            if attrs.get("price") is None or not attrs.get("sku"):
                raise serializers.ValidationError("Provide 'variants' or both 'price' and 'sku'.")
            if ProductVariant.objects.filter(sku=attrs["sku"]).exists():
                raise serializers.ValidationError({"sku": "A variant with this SKU already exists."})
        skus = [v["sku"] for v in attrs.get("variants") or []]
        if len(skus) != len(set(skus)):
            raise serializers.ValidationError({"variants": "SKUs must be unique."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        variants = validated_data.pop("variants", None)
        price = validated_data.pop("price", None)
        compare_at = validated_data.pop("compare_at_price", None)
        sku = validated_data.pop("sku", None)
        categories = validated_data.pop("categories", [])
        product = Product.objects.create(**validated_data)
        product.categories.set(categories)
        if not variants:
            variants = [{"sku": sku, "price": price, "compare_at_price": compare_at, "is_default": True}]
        for index, data in enumerate(variants):
            data.setdefault("is_default", index == 0)
            ProductVariant.objects.create(product=product, sort_order=index, **data)
        return product

    @transaction.atomic
    def update(self, instance, validated_data):
        for key in ("variants", "price", "compare_at_price", "sku"):
            validated_data.pop(key, None)
        return super().update(instance, validated_data)
