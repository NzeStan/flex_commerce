from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.tasks import enqueue

from .models import (
    DeviceToken,
    InAppNotification,
    NotificationLog,
    NotificationPreference,
    NotificationTemplate,
)


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "channel", "is_active", "updated_at")
    list_filter = ("channel", "event", "is_active")
    search_fields = ("name", "subject", "body")
    list_editable = ("is_active",)
    fieldsets = (
        (None, {"fields": ("name", "event", "channel", "is_active")}),
        (
            _("Content"),
            {
                "fields": ("subject", "body", "html_body"),
                "description": _("Django template syntax, e.g. {{ order_number }}, {% if carrier %}…{% endif %}."),
            },
        ),
    )


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "channel",
        "recipient",
        "status",
        "attempts",
        "provider",
        "sent_at",
        "created_at",
    )
    list_filter = ("channel", "status", "event", "provider")
    search_fields = ("recipient", "subject", "provider_message_id")
    readonly_fields = [f.name for f in NotificationLog._meta.concrete_fields]
    ordering = ("-created_at",)
    actions = ["retry"]

    def has_add_permission(self, request):
        return False

    @admin.action(description=_("Retry selected notifications"))
    def retry(self, request, queryset):
        ids = list(queryset.values_list("pk", flat=True))
        queryset.update(status=NotificationLog.STATUS_PENDING, attempts=0, next_attempt_at=timezone.now())
        for pk in ids:
            enqueue("flexcommerce_notifications.dispatcher.deliver", str(pk))


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "email_order_updates",
        "email_marketing",
        "sms_order_updates",
        "push_order_updates",
    )
    search_fields = ("user__email",)
    raw_id_fields = ("user",)


@admin.register(InAppNotification)
class InAppNotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "event", "title", "read_at", "created_at")
    list_filter = ("event",)
    search_fields = ("user__email", "title")
    raw_id_fields = ("user",)


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "is_active", "last_used_at")
    list_filter = ("platform", "is_active")
    raw_id_fields = ("user",)
