from django.contrib import admin

from .models import PriceBreakdown, TaxCategory


@admin.register(TaxCategory)
class TaxCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category_type", "rate")
    list_filter = ("category_type",)
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


@admin.register(PriceBreakdown)
class PriceBreakdownAdmin(admin.ModelAdmin):
    list_display = ("id", "quantity", "unit_price_net", "unit_vat", "unit_price_gross", "currency")
    readonly_fields = [f.name for f in PriceBreakdown._meta.fields]

    def has_add_permission(self, request):
        return False
