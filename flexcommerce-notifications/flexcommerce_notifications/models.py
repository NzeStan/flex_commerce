"""
FlexCommerce Notifications (outbox pattern).

* ``NotificationTemplate``   – admin overrides for any event + channel
* ``NotificationLog``        – the outbox: one row per message, retried with backoff
* ``NotificationPreference`` – per-user channel opt-in/out (transactional vs marketing)
* ``InAppNotification``      – the in-app inbox
* ``DeviceToken``            – push tokens (FCM / OneSignal) per device
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel

CHANNEL_EMAIL = "email"
CHANNEL_SMS = "sms"
CHANNEL_PUSH = "push"
CHANNEL_IN_APP = "in_app"
CHANNEL_CHOICES = [
    (CHANNEL_EMAIL, _("Email")),
    (CHANNEL_SMS, _("SMS")),
    (CHANNEL_PUSH, _("Push")),
    (CHANNEL_IN_APP, _("In-app")),
]


class NotificationTemplate(TimeStampedUUIDModel):
    """Override the built-in message for an event + channel. Uses Django template syntax."""

    CHANNEL_EMAIL = CHANNEL_EMAIL
    CHANNEL_SMS = CHANNEL_SMS
    CHANNEL_PUSH = CHANNEL_PUSH
    CHANNEL_IN_APP = CHANNEL_IN_APP
    CHANNEL_CHOICES = CHANNEL_CHOICES

    event = models.CharField(_("event"), max_length=100, db_index=True)
    channel = models.CharField(_("channel"), max_length=10, choices=CHANNEL_CHOICES)
    name = models.CharField(_("name"), max_length=200)
    subject = models.CharField(_("subject / title"), max_length=255, blank=True)
    body = models.TextField(_("body"), help_text=_("Django template syntax, e.g. {{ order_number }}."))
    html_body = models.TextField(_("HTML body (email)"), blank=True)
    is_active = models.BooleanField(_("is active"), default=True)

    class Meta:
        verbose_name = _("notification template")
        verbose_name_plural = _("notification templates")
        unique_together = [("event", "channel")]

    def __str__(self):
        return f"{self.name} [{self.channel} / {self.event}]"

    def render(self, context: dict) -> dict:
        from .rendering import render_string

        return {
            "subject": render_string(self.subject, context),
            "body": render_string(self.body, context),
            "html_body": render_string(self.html_body, context, autoescape=True) if self.html_body else "",
        }


class NotificationLog(TimeStampedUUIDModel):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_SENT, _("Sent")),
        (STATUS_FAILED, _("Failed")),
        (STATUS_SKIPPED, _("Skipped")),
    ]
    CHANNEL_EMAIL = CHANNEL_EMAIL
    CHANNEL_SMS = CHANNEL_SMS
    CHANNEL_PUSH = CHANNEL_PUSH
    CHANNEL_IN_APP = CHANNEL_IN_APP
    CHANNEL_CHOICES = CHANNEL_CHOICES

    event = models.CharField(_("event"), max_length=100, db_index=True)
    channel = models.CharField(_("channel"), max_length=10, choices=CHANNEL_CHOICES)
    recipient = models.CharField(_("recipient"), max_length=255, help_text=_("Email, phone, device token or user id."))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_logs",
    )
    subject = models.CharField(_("subject"), max_length=255, blank=True)
    body = models.TextField(_("body"), blank=True)
    html_body = models.TextField(_("HTML body"), blank=True)
    data = models.JSONField(_("data"), default=dict, blank=True, help_text=_("Push / in-app payload (e.g. deep link)."))
    status = models.CharField(_("status"), max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    attempts = models.PositiveIntegerField(_("attempts"), default=0)
    next_attempt_at = models.DateTimeField(_("next attempt at"), null=True, blank=True)
    error_message = models.TextField(_("error message"), blank=True)
    provider = models.CharField(_("provider"), max_length=100, blank=True)
    provider_message_id = models.CharField(_("provider message ID"), max_length=255, blank=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    context_snapshot = models.JSONField(_("context snapshot"), default=dict, blank=True)
    dedupe_key = models.CharField(_("dedupe key"), max_length=255, null=True, blank=True, unique=True)

    class Meta:
        verbose_name = _("notification log")
        verbose_name_plural = _("notification logs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event", "status"], name="notif_event_status_idx"),
            models.Index(fields=["recipient", "channel"], name="notif_recipient_idx"),
            models.Index(fields=["user", "channel"], name="notif_user_channel_idx"),
            models.Index(fields=["status", "next_attempt_at"], name="notif_due_idx"),
        ]

    def __str__(self):
        return f"[{self.channel}] {self.event} → {self.recipient} ({self.status})"


class NotificationPreference(TimeStampedUUIDModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    email_order_updates = models.BooleanField(_("email: order updates"), default=True)
    email_marketing = models.BooleanField(_("email: offers & reminders"), default=False)
    email_abandoned_cart = models.BooleanField(_("email: abandoned cart"), default=True)
    sms_order_updates = models.BooleanField(_("SMS: order updates"), default=True)
    sms_marketing = models.BooleanField(_("SMS: offers & reminders"), default=False)
    push_order_updates = models.BooleanField(_("push: order updates"), default=True)
    push_marketing = models.BooleanField(_("push: offers & reminders"), default=False)
    in_app_marketing = models.BooleanField(_("in-app: offers & reminders"), default=True)

    class Meta:
        verbose_name = _("notification preference")
        verbose_name_plural = _("notification preferences")

    def __str__(self):
        return f"NotifPrefs({self.user})"

    MARKETING_EVENTS = {"cart.abandoned", "cart.recovered", "wishlist.price_drop", "marketing"}

    def allows(self, channel: str, event: str) -> bool:
        is_marketing = event in self.MARKETING_EVENTS
        if event == "cart.abandoned" and channel == CHANNEL_EMAIL:
            return self.email_abandoned_cart
        if channel == CHANNEL_IN_APP:
            return self.in_app_marketing if is_marketing else True  # order updates always land in the inbox
        field = f"{channel}_{'marketing' if is_marketing else 'order_updates'}"
        return getattr(self, field, True)


class InAppNotification(TimeStampedUUIDModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="inbox")
    event = models.CharField(_("event"), max_length=100)
    title = models.CharField(_("title"), max_length=255)
    body = models.TextField(_("body"))
    data = models.JSONField(_("data"), default=dict, blank=True)
    read_at = models.DateTimeField(_("read at"), null=True, blank=True)

    class Meta:
        verbose_name = _("in-app notification")
        verbose_name_plural = _("in-app notifications")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"], name="notif_inbox_unread_idx")]

    def __str__(self):
        return f"{self.user}: {self.title}"


class DeviceToken(TimeStampedUUIDModel):
    PLATFORM_CHOICES = [("android", "Android"), ("ios", "iOS"), ("web", "Web")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_tokens")
    token = models.CharField(_("token"), max_length=512, unique=True)
    platform = models.CharField(_("platform"), max_length=10, choices=PLATFORM_CHOICES, default="android")
    is_active = models.BooleanField(_("is active"), default=True)
    last_used_at = models.DateTimeField(_("last used"), null=True, blank=True)

    class Meta:
        verbose_name = _("device token")
        verbose_name_plural = _("device tokens")

    def __str__(self):
        return f"{self.platform}:{self.token[:12]}…"
