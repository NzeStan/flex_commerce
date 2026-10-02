"""
FlexCommerce core models.

* ``TimeStampedUUIDModel`` / ``SoftDeleteModel`` — abstract bases used by every package.
* ``AuditLog`` — generic, append-only audit trail.
* ``WebhookEndpoint`` / ``WebhookDelivery`` — signed outgoing webhooks with retries.
* ``Address`` — customer address book.
"""

import secrets
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .conf import fc_setting

# ── Abstract Base Models ──────────────────────────────────────────────────────


class TimeStampedUUIDModel(models.Model):
    """Base model: UUID PK + timestamps + free-form ``extra_data``."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("ID"))
    created_at = models.DateTimeField(_("created at"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    extra_data = models.JSONField(_("extra data"), default=dict, blank=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.pk}>"


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)

    def delete(self):
        return super().update(deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    def get_queryset(self):
        return super().get_queryset().alive()

    def all_with_deleted(self):
        return SoftDeleteQuerySet(self.model, using=self._db)

    def deleted_only(self):
        return SoftDeleteQuerySet(self.model, using=self._db).deleted()


class SoftDeleteModel(TimeStampedUUIDModel):
    """Adds soft-delete. Honors ``FLEXCOMMERCE["SOFT_DELETE"]``."""

    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()  # noqa: DJ012 - second manager, not a field

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        if not fc_setting("SOFT_DELETE", True):
            return super().delete(using=using, keep_parents=keep_parents)
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])
        return (1, {self._meta.label: 1})

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=["deleted_at", "updated_at"])

    @property
    def is_deleted(self):
        return self.deleted_at is not None


# ── AuditLog ──────────────────────────────────────────────────────────────────


class AuditLog(TimeStampedUUIDModel):
    """Generic audit log for any model with a UUID primary key."""

    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_TRANSITION = "transition"
    ACTION_CHOICES = [
        (ACTION_CREATE, _("Create")),
        (ACTION_UPDATE, _("Update")),
        (ACTION_DELETE, _("Delete")),
        (ACTION_TRANSITION, _("Transition")),
    ]

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        verbose_name=_("content type"),
    )
    object_id = models.UUIDField(_("object ID"), db_index=True)
    content_object = GenericForeignKey("content_type", "object_id")

    action = models.CharField(_("action"), max_length=20, choices=ACTION_CHOICES)
    actor = models.CharField(_("actor"), max_length=255, blank=True)
    changes = models.JSONField(_("changes"), default=dict, blank=True)
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    note = models.TextField(_("note"), blank=True)

    class Meta:
        verbose_name = _("audit log")
        verbose_name_plural = _("audit logs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"], name="core_audit_ct_idx"),
            models.Index(fields=["action", "created_at"], name="core_audit_action_idx"),
        ]

    def __str__(self):
        return f"{self.action} on {self.content_type} {self.object_id}"

    @classmethod
    def log(cls, instance, action, actor="system", changes=None, ip=None, note=""):
        if not fc_setting("AUDIT_ENABLED", True):
            return None
        from .signals import audit_logged

        entry = cls.objects.create(
            content_type=ContentType.objects.get_for_model(instance),
            object_id=instance.pk,
            action=action,
            actor=str(actor)[:255],
            changes=changes or {},
            ip_address=ip,
            note=note or "",
        )
        audit_logged.send(sender=cls, instance=instance, action=action, actor=actor, entry=entry)
        return entry


# ── Webhooks ──────────────────────────────────────────────────────────────────


class WebhookEndpoint(TimeStampedUUIDModel):
    """
    An external URL subscribed to a FlexCommerce event (or ``"*"`` for all events).

    Payloads are signed: ``X-FlexCommerce-Signature: t=<unix>,v1=<hex hmac-sha256>``
    computed over ``"<t>.<raw body>"`` with ``secret``.
    """

    EVENT_CHOICES = [
        ("*", _("All events")),
        ("order.created", _("Order Created")),
        ("order.paid", _("Order Paid")),
        ("order.confirmed", _("Order Confirmed")),
        ("order.processing", _("Order Processing")),
        ("order.shipped", _("Order Shipped")),
        ("order.delivered", _("Order Delivered")),
        ("order.cancelled", _("Order Cancelled")),
        ("order.refunded", _("Order Refunded")),
        ("refund.processed", _("Refund Processed")),
        ("return.requested", _("Return Requested")),
        ("return.updated", _("Return Updated")),
        ("shipment.created", _("Shipment Created")),
        ("shipment.updated", _("Shipment Updated")),
        ("cart.abandoned", _("Cart Abandoned")),
        ("payment.success", _("Payment Success")),
        ("payment.failed", _("Payment Failed")),
        ("inventory.low_stock", _("Low Stock")),
        ("inventory.out_of_stock", _("Out of Stock")),
        ("inventory.restocked", _("Restocked")),
        ("review.submitted", _("Review Submitted")),
        ("vendor.approved", _("Vendor Approved")),
        ("payout.created", _("Payout Created")),
    ]

    url = models.URLField(_("URL"), max_length=500)
    event = models.CharField(_("event"), max_length=100, choices=EVENT_CHOICES)
    secret = models.CharField(_("secret"), max_length=255, blank=True)
    description = models.CharField(_("description"), max_length=255, blank=True)
    is_active = models.BooleanField(_("is active"), default=True)
    last_triggered_at = models.DateTimeField(_("last triggered at"), null=True, blank=True)
    failure_count = models.PositiveIntegerField(
        _("consecutive failures"), default=0, help_text=_("Reset on every successful delivery.")
    )

    class Meta:
        verbose_name = _("webhook endpoint")
        verbose_name_plural = _("webhook endpoints")
        unique_together = [("url", "event")]

    def __str__(self):
        return f"{self.event} → {self.url}"

    def save(self, *args, **kwargs):
        if not self.secret:
            self.secret = secrets.token_hex(32)
        super().save(*args, **kwargs)


class WebhookDelivery(TimeStampedUUIDModel):
    """Outbox row for a single webhook delivery (with retry bookkeeping)."""

    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_SUCCESS, _("Success")),
        (STATUS_FAILED, _("Failed")),
    ]

    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(_("event"), max_length=100)
    payload = models.JSONField(_("payload"), default=dict)
    status = models.CharField(_("status"), max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    attempts = models.PositiveIntegerField(_("attempts"), default=0)
    next_attempt_at = models.DateTimeField(_("next attempt at"), null=True, blank=True)
    response_status = models.PositiveIntegerField(_("response status"), null=True, blank=True)
    response_body = models.TextField(_("response body"), blank=True)
    last_error = models.TextField(_("last error"), blank=True)
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True)

    class Meta:
        verbose_name = _("webhook delivery")
        verbose_name_plural = _("webhook deliveries")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="core_whdelivery_due_idx")]

    def __str__(self):
        return f"{self.event} → {self.endpoint.url} ({self.status})"


# ── Address ───────────────────────────────────────────────────────────────────


class Address(TimeStampedUUIDModel):
    """Customer address book entry (Nigeria-friendly: LGA + landmark)."""

    TYPE_BILLING = "billing"
    TYPE_SHIPPING = "shipping"
    TYPE_CHOICES = [
        (TYPE_BILLING, _("Billing")),
        (TYPE_SHIPPING, _("Shipping")),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="addresses",
        verbose_name=_("user"),
    )
    address_type = models.CharField(_("type"), max_length=20, choices=TYPE_CHOICES, default=TYPE_SHIPPING)
    label = models.CharField(_("label"), max_length=50, blank=True, help_text=_('e.g. "Home", "Office"'))
    first_name = models.CharField(_("first name"), max_length=100)
    last_name = models.CharField(_("last name"), max_length=100)
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("phone"), max_length=20, blank=True)
    alt_phone = models.CharField(_("alternative phone"), max_length=20, blank=True)
    line1 = models.CharField(_("address line 1"), max_length=255)
    line2 = models.CharField(_("address line 2"), max_length=255, blank=True)
    landmark = models.CharField(_("landmark"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=100)
    lga = models.CharField(_("LGA"), max_length=100, blank=True, help_text=_("Local Government Area"))
    state = models.CharField(_("state"), max_length=100)
    country = models.CharField(_("country"), max_length=100, default="Nigeria")
    postal_code = models.CharField(_("postal code"), max_length=20, blank=True)
    is_default = models.BooleanField(_("is default"), default=False)

    class Meta:
        verbose_name = _("address")
        verbose_name_plural = _("addresses")
        ordering = ["-is_default", "-created_at"]
        indexes = [models.Index(fields=["user", "address_type"], name="core_addr_user_idx")]

    def __str__(self):
        return f"{self.first_name} {self.last_name}, {self.city}, {self.state}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    SNAPSHOT_FIELDS = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "alt_phone",
        "line1",
        "line2",
        "landmark",
        "city",
        "lga",
        "state",
        "country",
        "postal_code",
    )

    def to_snapshot(self) -> dict:
        """Plain dict suitable for storing on an order."""
        return {f: getattr(self, f) for f in self.SNAPSHOT_FIELDS}
