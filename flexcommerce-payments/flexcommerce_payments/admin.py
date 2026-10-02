from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.exceptions import FlexCommerceError

from .models import Payment, PaymentWebhookLog, Wallet, WalletTransaction
from .services import PaymentService


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "provider",
        "purpose",
        "order",
        "amount",
        "currency",
        "status",
        "channel",
        "created_at",
    )
    list_filter = ("status", "provider", "purpose", "channel")
    search_fields = ("reference", "provider_reference", "order__order_number", "email")
    readonly_fields = [f.name for f in Payment._meta.concrete_fields]
    raw_id_fields = ("order", "user")
    actions = ["reverify"]

    def has_add_permission(self, request):
        return False

    @admin.action(description=_("Re-verify selected payments with the provider"))
    def reverify(self, request, queryset):
        for payment in queryset:
            try:
                PaymentService.verify(payment.reference)
            except FlexCommerceError as exc:
                self.message_user(request, f"{payment.reference}: {exc.message}", messages.WARNING)


@admin.register(PaymentWebhookLog)
class PaymentWebhookLogAdmin(admin.ModelAdmin):
    list_display = ("provider", "event_type", "reference", "signature_valid", "processed", "error", "created_at")
    list_filter = ("provider", "signature_valid", "processed")
    search_fields = ("reference", "event_id")
    readonly_fields = [f.name for f in PaymentWebhookLog._meta.concrete_fields]

    def has_add_permission(self, request):
        return False


class WalletTransactionInline(admin.TabularInline):
    model = WalletTransaction
    extra = 0
    can_delete = False
    fields = ("created_at", "type", "source", "amount", "balance_after", "reference", "description")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "currency", "is_active", "updated_at")
    list_filter = ("is_active", "currency")
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user",)
    readonly_fields = ("balance",)  # balances change only through the audited ledger
    inlines = [WalletTransactionInline]
