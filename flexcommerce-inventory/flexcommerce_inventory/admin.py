from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import InventoryItem, StockAlert, StockMovement, StockReservation


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    can_delete = False
    readonly_fields = ("movement_type", "quantity", "note", "reference", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "on_hand",
        "reserved",
        "available",
        "sold",
        "reorder_point",
        "allow_oversell",
    )
    list_filter = ("allow_oversell",)
    search_fields = ("sku", "object_id")
    # Stock levels only change through the audited service methods / API.
    readonly_fields = ("on_hand", "reserved", "sold", "available")
    inlines = [StockMovementInline]

    @admin.display(description=_("available"))
    def available(self, obj):
        return obj.available


@admin.register(StockReservation)
class StockReservationAdmin(admin.ModelAdmin):
    list_display = ("inventory_item", "quantity", "status", "order_ref", "expires_at", "created_at")
    list_filter = ("status",)
    search_fields = ("order_ref",)
    raw_id_fields = ("inventory_item",)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("inventory_item", "movement_type", "quantity", "reference", "created_at")
    list_filter = ("movement_type",)
    search_fields = ("reference", "inventory_item__sku")
    raw_id_fields = ("inventory_item",)

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StockAlert)
class StockAlertAdmin(admin.ModelAdmin):
    list_display = ("object_id", "user", "email", "notified_at", "created_at")
    list_filter = ("notified_at",)
    search_fields = ("email", "user__email")
    raw_id_fields = ("user",)
