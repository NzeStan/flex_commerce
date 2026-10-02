from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Cart, CartItem, SavedItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    can_delete = False
    fields = (
        "content_type",
        "object_id",
        "quantity",
        "unit_price_with_tax",
        "vat_amount",
        "discount_amount",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "email",
        "status",
        "items_count",
        "total_amount",
        "currency",
        "last_activity",
    )
    list_filter = ("status", "currency")
    search_fields = ("user__email", "email", "session_key", "coupon_code", "token")
    readonly_fields = (
        "id",
        "token",
        "created_at",
        "updated_at",
        "last_activity",
        "subtotal_amount",
        "tax_amount",
        "total_amount",
        "items_count",
        "discount_amount",
        "abandoned_notified_at",
    )
    raw_id_fields = ("user",)
    inlines = [CartItemInline]
    ordering = ("-last_activity",)
    list_select_related = ("user",)
    actions = ["mark_abandoned", "mark_expired"]

    @admin.action(description=_("Mark selected carts as abandoned"))
    def mark_abandoned(self, request, queryset):
        queryset.update(status=Cart.STATUS_ABANDONED)

    @admin.action(description=_("Mark selected carts as expired"))
    def mark_expired(self, request, queryset):
        queryset.update(status=Cart.STATUS_EXPIRED)


@admin.register(SavedItem)
class SavedItemAdmin(admin.ModelAdmin):
    list_display = ("user", "object_id", "saved_price", "created_at")
    search_fields = ("user__email",)
    raw_id_fields = ("user",)
