from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from . import services
from .models import (
    ProductAnswer,
    ProductQuestion,
    ProductReview,
    SearchTerm,
    Wishlist,
    WishlistItem,
)


class WishlistItemInline(admin.TabularInline):
    model = WishlistItem
    extra = 0
    readonly_fields = ("content_type", "object_id", "note", "added_price", "created_at")


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "is_default", "visibility", "created_at")
    list_filter = ("visibility", "is_default")
    raw_id_fields = ("user",)
    inlines = [WishlistItemInline]


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "object_id",
        "rating",
        "title",
        "is_verified_purchase",
        "is_approved",
        "is_rejected",
        "is_featured",
        "helpful_count",
        "created_at",
    )
    list_filter = ("is_approved", "is_rejected", "is_featured", "rating", "is_verified_purchase")
    search_fields = ("user__email", "title", "body")
    raw_id_fields = ("user",)
    actions = ["approve_reviews", "reject_reviews"]

    @admin.action(description=_("Approve selected reviews"))
    def approve_reviews(self, request, queryset):
        for review in queryset:
            services.moderate_review(review, True)

    @admin.action(description=_("Reject selected reviews"))
    def reject_reviews(self, request, queryset):
        for review in queryset:
            services.moderate_review(review, False)


class ProductAnswerInline(admin.TabularInline):
    model = ProductAnswer
    extra = 0
    raw_id_fields = ("user",)


@admin.register(ProductQuestion)
class ProductQuestionAdmin(admin.ModelAdmin):
    list_display = ("question", "user", "is_approved", "created_at")
    list_filter = ("is_approved",)
    search_fields = ("question",)
    raw_id_fields = ("user",)
    inlines = [ProductAnswerInline]


@admin.register(SearchTerm)
class SearchTermAdmin(admin.ModelAdmin):
    list_display = ("term", "count", "is_hidden", "updated_at")
    list_editable = ("is_hidden",)
    search_fields = ("term",)
