from django.contrib import admin

from .models import PickupStation, ShippingMethod, ShippingZone


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "zone_code", "is_active", "sort_order")
    list_filter = ("is_active",)
    list_editable = ("is_active", "sort_order")
    search_fields = ("name", "zone_code")


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "carrier",
        "rate_type",
        "base_rate",
        "free_shipping_threshold",
        "is_pickup",
        "is_active",
        "sort_order",
    )
    list_filter = ("rate_type", "is_active", "is_pickup", "pay_on_delivery")
    filter_horizontal = ("zones",)
    list_editable = ("is_active", "sort_order")
    search_fields = ("name", "carrier", "code")


@admin.register(PickupStation)
class PickupStationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "city", "state", "zone", "fee", "is_active")
    list_filter = ("state", "is_active", "zone")
    search_fields = ("name", "code", "city", "address")
