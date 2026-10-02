from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Brand, Category, Product, ProductImage, ProductVariant


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("__str__", "slug", "is_active", "sort_order", "depth")
    list_filter = ("is_active", "depth")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ("parent",)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "is_official_store")
    list_filter = ("is_active", "is_official_store")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


class VariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = (
        "sku",
        "name",
        "attributes",
        "price",
        "compare_at_price",
        "cost_price",
        "weight",
        "is_active",
        "is_default",
    )


class ImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ("image", "url", "alt_text", "variant", "is_primary", "sort_order")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "status", "min_price", "in_stock", "rating_avg", "sold_count", "is_featured")
    list_filter = ("status", "is_featured", "in_stock", "brand")
    search_fields = ("name", "slug", "variants__sku")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("categories",)
    readonly_fields = (
        "min_price",
        "max_price",
        "compare_at_price",
        "rating_avg",
        "rating_count",
        "sold_count",
        "published_at",
    )
    inlines = [VariantInline, ImageInline]
    list_select_related = ("brand",)
    actions = ["publish", "archive"]

    @admin.action(description=_("Publish selected products"))
    def publish(self, request, queryset):
        for product in queryset:
            product.status = Product.STATUS_ACTIVE
            product.save(update_fields=["status", "updated_at"])

    @admin.action(description=_("Archive selected products"))
    def archive(self, request, queryset):
        queryset.update(status=Product.STATUS_ARCHIVED)


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("sku", "product", "name", "price", "is_active")
    search_fields = ("sku", "product__name", "barcode")
    list_filter = ("is_active",)
    raw_id_fields = ("product",)
    list_select_related = ("product",)
