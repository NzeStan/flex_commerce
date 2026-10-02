from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from . import services
from .models import Payout, Vendor, VendorMember, VendorOrder


class VendorMemberInline(admin.TabularInline):
    model = VendorMember
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "status", "is_official_store", "commission_rate", "rating_avg", "created_at")
    list_filter = ("status", "is_official_store")
    search_fields = ("name", "email", "owner__email", "business_registration_number")
    raw_id_fields = ("owner",)
    readonly_fields = ("rating_avg", "rating_count", "approved_at")
    inlines = [VendorMemberInline]
    actions = ["approve", "suspend"]

    @admin.action(description=_("Approve selected vendors"))
    def approve(self, request, queryset):
        for vendor in queryset:
            services.set_status(vendor, Vendor.STATUS_APPROVED)

    @admin.action(description=_("Suspend selected vendors"))
    def suspend(self, request, queryset):
        for vendor in queryset:
            services.set_status(vendor, Vendor.STATUS_SUSPENDED)


@admin.register(VendorOrder)
class VendorOrderAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "vendor",
        "status",
        "gross_amount",
        "commission_amount",
        "refunded_amount",
        "available_at",
        "payout",
    )
    list_filter = ("status", "vendor")
    search_fields = ("order__order_number", "vendor__name")
    raw_id_fields = ("order", "vendor", "payout")


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ("vendor", "amount", "currency", "status", "reference", "processed_at", "created_at")
    list_filter = ("status",)
    search_fields = ("vendor__name", "reference")
    raw_id_fields = ("vendor",)
