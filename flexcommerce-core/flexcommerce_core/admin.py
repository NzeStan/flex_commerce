from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Address, AuditLog, WebhookDelivery, WebhookEndpoint


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "content_type", "object_id", "actor", "created_at")
    list_filter = ("action", "content_type")
    search_fields = ("actor", "note", "object_id")
    readonly_fields = (
        "id",
        "content_type",
        "object_id",
        "action",
        "actor",
        "changes",
        "ip_address",
        "note",
        "created_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ("event", "url", "is_active", "failure_count", "last_triggered_at")
    list_filter = ("event", "is_active")
    search_fields = ("url", "event", "description")
    list_editable = ("is_active",)
    readonly_fields = ("failure_count", "last_triggered_at")
    fieldsets = (
        (None, {"fields": ("url", "event", "description", "is_active")}),
        (
            _("Security"),
            {"fields": ("secret",), "description": _("Leave blank to generate a random secret.")},
        ),
        (_("Status"), {"fields": ("failure_count", "last_triggered_at")}),
    )


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "endpoint",
        "status",
        "attempts",
        "response_status",
        "created_at",
        "delivered_at",
    )
    list_filter = ("status", "event")
    search_fields = ("endpoint__url", "event")
    readonly_fields = [f.name for f in WebhookDelivery._meta.fields]
    actions = ["retry_now"]

    def has_add_permission(self, request):
        return False

    @admin.action(description=_("Retry selected deliveries now"))
    def retry_now(self, request, queryset):
        from django.utils import timezone

        from .tasks import enqueue

        ids = list(queryset.values_list("pk", flat=True))
        queryset.update(status=WebhookDelivery.STATUS_PENDING, next_attempt_at=timezone.now(), attempts=0)
        for pk in ids:
            enqueue("flexcommerce_core.webhooks.deliver", str(pk))
        self.message_user(request, _("%d deliveries re-queued.") % len(ids))


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("full_name", "city", "state", "country", "address_type", "is_default", "user")
    list_filter = ("address_type", "country", "state")
    search_fields = ("first_name", "last_name", "email", "phone", "city", "line1")
    raw_id_fields = ("user",)
