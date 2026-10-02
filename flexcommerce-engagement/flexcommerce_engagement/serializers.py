from rest_framework import serializers

from flexcommerce_core.utils.products import product_name

from .models import (
    ProductAnswer,
    ProductQuestion,
    ProductReview,
    RecentlyViewed,
    RecentSearch,
    SearchTerm,
    Wishlist,
    WishlistItem,
)


class ProductRefSerializer(serializers.Serializer):
    product_type = serializers.CharField(required=False, allow_blank=True, max_length=150)
    product_model = serializers.CharField(required=False, allow_blank=True, max_length=150, help_text="Alias")
    product_id = serializers.UUIDField()

    def validate(self, attrs):
        attrs["product_type"] = attrs.get("product_type") or attrs.pop("product_model", "") or ""
        return attrs


def _product_info(obj):
    product = obj.product
    return {
        "type": obj.content_type.model_class()._meta.label_lower if obj.content_type_id else None,
        "id": str(obj.object_id),
        "name": product_name(product) if product is not None else None,
        "slug": getattr(product, "slug", None) if product is not None else None,
    }


class WishlistItemSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="object_id", read_only=True)
    product = serializers.SerializerMethodField()

    class Meta:
        model = WishlistItem
        fields = ["id", "product_id", "product", "note", "added_price", "created_at"]

    def get_product(self, obj):
        return _product_info(obj)


class WishlistSerializer(serializers.ModelSerializer):
    items = WishlistItemSerializer(many=True, read_only=True)

    class Meta:
        model = Wishlist
        fields = ["id", "name", "is_default", "visibility", "share_token", "items", "created_at"]
        read_only_fields = ["id", "is_default", "share_token", "items", "created_at"]


class AddToWishlistSerializer(ProductRefSerializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ProductReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    product_id = serializers.UUIDField(source="object_id", read_only=True)
    has_voted = serializers.SerializerMethodField()

    class Meta:
        model = ProductReview
        fields = [
            "id",
            "product_id",
            "rating",
            "title",
            "body",
            "image_urls",
            "is_verified_purchase",
            "is_approved",
            "is_featured",
            "helpful_count",
            "has_voted",
            "reply",
            "replied_at",
            "user_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "product_id",
            "is_verified_purchase",
            "is_approved",
            "is_featured",
            "helpful_count",
            "has_voted",
            "reply",
            "replied_at",
            "user_name",
            "created_at",
            "updated_at",
        ]

    def get_user_name(self, obj):
        full = obj.user.get_full_name() if hasattr(obj.user, "get_full_name") else ""
        if full:
            parts = full.split()
            return f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else parts[0]
        return "Customer"

    def get_has_voted(self, obj):
        voted = self.context.get("voted_ids")
        return obj.pk in voted if voted is not None else False

    def validate_image_urls(self, value):
        if len(value) > 6:
            raise serializers.ValidationError("At most 6 photos.")
        url = serializers.URLField(max_length=500)
        return [url.run_validation(v) for v in value]


class ReviewCreateSerializer(ProductRefSerializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    body = serializers.CharField(max_length=5000)
    image_urls = serializers.ListField(child=serializers.URLField(max_length=500), required=False, max_length=6)


class ModerationSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ReplySerializer(serializers.Serializer):
    reply = serializers.CharField(max_length=5000)


class ProductAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductAnswer
        fields = ["id", "answer", "is_official", "created_at"]
        read_only_fields = ["id", "is_official", "created_at"]


class ProductQuestionSerializer(serializers.ModelSerializer):
    answers = serializers.SerializerMethodField()
    product_id = serializers.UUIDField(source="object_id", read_only=True)

    class Meta:
        model = ProductQuestion
        fields = ["id", "product_id", "question", "is_approved", "answers", "created_at"]
        read_only_fields = ["id", "product_id", "is_approved", "answers", "created_at"]

    def get_answers(self, obj):
        return ProductAnswerSerializer([a for a in obj.answers.all() if a.is_approved], many=True).data


class QuestionCreateSerializer(ProductRefSerializer):
    question = serializers.CharField(max_length=1000)


class RecentSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecentSearch
        fields = ["id", "query", "updated_at"]


class SearchQuerySerializer(serializers.Serializer):
    query = serializers.CharField(max_length=255)


class SearchTermSerializer(serializers.ModelSerializer):
    class Meta:
        model = SearchTerm
        fields = ["term", "count"]


class RecentlyViewedSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="object_id", read_only=True)
    product = serializers.SerializerMethodField()

    class Meta:
        model = RecentlyViewed
        fields = ["id", "product_id", "product", "view_count", "updated_at"]

    def get_product(self, obj):
        return _product_info(obj)
