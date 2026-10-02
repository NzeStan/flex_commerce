from django.contrib import admin

from .models import Coupon, CouponRedemption, CouponUsage, FlashSale, FlashSaleItem


class CouponUsageInline(admin.TabularInline):
    model = CouponUsage
    extra = 0
    can_delete = False
    readonly_fields = ("user", "email", "order_ref", "discount_applied", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "coupon_type",
        "value",
        "used_count",
        "usage_limit",
        "expires_at",
        "is_active",
        "auto_apply",
        "is_valid",
    )
    list_filter = ("coupon_type", "is_active", "auto_apply", "first_order_only")
    search_fields = ("code", "name")
    readonly_fields = ("used_count",)
    inlines = [CouponUsageInline]

    @admin.display(boolean=True)
    def is_valid(self, obj):
        return obj.is_valid


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ("coupon", "customer_key", "count")
    search_fields = ("coupon__code", "customer_key")
    raw_id_fields = ("coupon",)


class FlashSaleItemInline(admin.TabularInline):
    model = FlashSaleItem
    extra = 0
    fields = ("content_type", "object_id", "sale_price", "quantity_limit", "sold_quantity")
    readonly_fields = ("sold_quantity",)


@admin.register(FlashSale)
class FlashSaleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "discount_percentage",
        "starts_at",
        "ends_at",
        "is_active",
        "is_running",
    )
    list_filter = ("is_active",)
    inlines = [FlashSaleItemInline]

    @admin.display(boolean=True)
    def is_running(self, obj):
        return obj.is_running
