from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.exceptions import FlexCommerceError

from .models import Order, OrderEvent, OrderItem, Refund, ReturnRequest, Shipment, ShipmentEvent
from .services import OrderService


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = (
        "product_name",
        "product_sku",
        "quantity",
        "unit_price_gross",
        "discount_amount",
        "line_total",
        "quantity_shipped",
        "quantity_returned",
        "vendor_id",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class ShipmentInline(admin.TabularInline):
    model = Shipment
    extra = 0
    fields = ("carrier", "tracking_number", "status", "shipped_at", "delivered_at")
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    fields = ("amount", "method", "status", "reason", "processed_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class OrderEventInline(admin.TabularInline):
    model = OrderEvent
    extra = 0
    can_delete = False
    fields = ("created_at", "event", "message", "actor", "is_customer_visible")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


STATUS_COLORS = {
    "pending": "#b26a00",
    "confirmed": "#1565c0",
    "processing": "#6a1b9a",
    "partially_shipped": "#00838f",
    "shipped": "#00838f",
    "delivered": "#2e7d32",
    "cancelled": "#c62828",
    "refunded": "#616161",
    "partially_refunded": "#795548",
}


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "customer",
        "colored_status",
        "payment_status",
        "payment_method",
        "grand_total",
        "currency",
        "created_at",
    )
    list_filter = ("status", "payment_status", "payment_method", "currency")
    search_fields = ("order_number", "email", "phone", "user__email", "payment_reference")
    readonly_fields = [f.name for f in Order._meta.concrete_fields if f.name not in ("internal_note",)]
    inlines = [OrderItemInline, ShipmentInline, RefundInline, OrderEventInline]
    ordering = ("-created_at",)
    list_select_related = ("user",)
    actions = ["confirm_orders", "mark_processing", "mark_delivered", "cancel_orders"]

    def has_add_permission(self, request):
        return False

    @admin.display(description=_("Customer"))
    def customer(self, obj):
        return obj.customer_email or "-"

    @admin.display(description=_("Status"))
    def colored_status(self, obj):
        return format_html(
            '<b style="color:{}">{}</b>',
            STATUS_COLORS.get(obj.status, "#000"),
            obj.get_status_display(),
        )

    def _bulk(self, request, queryset, method, label, **kwargs):
        done = 0
        for order in queryset:
            try:
                getattr(OrderService(order), method)(actor=str(request.user), **kwargs)
                done += 1
            except FlexCommerceError as exc:
                self.message_user(request, f"{order.order_number}: {exc.message}", messages.WARNING)
        self.message_user(request, _("%(n)d orders %(label)s.") % {"n": done, "label": label})

    @admin.action(description=_("Confirm selected orders"))
    def confirm_orders(self, request, queryset):
        self._bulk(request, queryset, "confirm", "confirmed")

    @admin.action(description=_("Mark as processing"))
    def mark_processing(self, request, queryset):
        self._bulk(request, queryset, "transition", "moved to processing", to_state=Order.STATUS_PROCESSING)

    @admin.action(description=_("Mark as delivered"))
    def mark_delivered(self, request, queryset):
        self._bulk(request, queryset, "mark_delivered", "delivered")

    @admin.action(description=_("Cancel selected orders (releases stock, restores coupons)"))
    def cancel_orders(self, request, queryset):
        self._bulk(request, queryset, "cancel", "cancelled", reason="Cancelled by staff")


class ShipmentEventInline(admin.TabularInline):
    model = ShipmentEvent
    extra = 0
    fields = ("occurred_at", "status", "description", "location")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("order", "carrier", "tracking_number", "status", "shipped_at", "delivered_at")
    list_filter = ("status", "carrier")
    search_fields = ("tracking_number", "order__order_number")
    raw_id_fields = ("order",)
    inlines = [ShipmentEventInline]


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("order", "amount", "method", "status", "processed_by", "processed_at")
    list_filter = ("status", "method")
    search_fields = ("order__order_number", "reference")
    raw_id_fields = ("order", "processed_by", "return_request")
    readonly_fields = ("status", "processed_by", "processed_at", "reference", "failure_reason")
    actions = ["process_refunds"]

    @admin.action(description=_("Process selected refunds (pays them out)"))
    def process_refunds(self, request, queryset):
        done = 0
        for refund in queryset.select_related("order"):
            try:
                OrderService(refund.order).process_refund(refund, actor=str(request.user), processed_by=request.user)
                done += 1
            except FlexCommerceError as exc:
                self.message_user(request, f"{refund.order.order_number}: {exc.message}", messages.WARNING)
        self.message_user(request, _("%d refunds processed.") % done)


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = ("order", "reason_code", "status", "refund_method", "created_at")
    list_filter = ("status", "reason_code")
    search_fields = ("order__order_number",)
    raw_id_fields = ("order", "user", "approved_by")
