"""
FlexCommerce Inventory.

Concurrency-safe stock for any product model. Every stock change is a single
conditional ``UPDATE`` (no read-modify-write races, no long row locks), and every
change is recorded as a ``StockMovement``.

Products without an ``InventoryItem`` are *untracked* (unlimited) unless
``FLEXCOMMERCE["INVENTORY_TRACK_BY_DEFAULT"]`` is True.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.functions import Greatest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.conf import fc_setting
from flexcommerce_core.exceptions import InsufficientStockError, StockReservationError
from flexcommerce_core.models import TimeStampedUUIDModel

from .conf import inventory_setting


class InventoryItem(TimeStampedUUIDModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, verbose_name=_("product type"))
    object_id = models.UUIDField(_("product ID"), db_index=True)
    product = GenericForeignKey("content_type", "object_id")

    sku = models.CharField(_("SKU"), max_length=100, blank=True, db_index=True)
    on_hand = models.PositiveIntegerField(_("on hand"), default=0)
    reserved = models.PositiveIntegerField(_("reserved"), default=0)
    sold = models.PositiveIntegerField(_("sold"), default=0)
    reorder_point = models.PositiveIntegerField(
        _("low-stock threshold"),
        null=True,
        blank=True,
        help_text=_("Alert when available stock falls to this level. Blank = global LOW_STOCK_THRESHOLD."),
    )
    allow_oversell = models.BooleanField(
        _("allow oversell / backorder"),
        default=False,
        help_text=_("Allow purchases beyond on-hand stock."),
    )

    class Meta:
        verbose_name = _("inventory item")
        verbose_name_plural = _("inventory items")
        unique_together = [("content_type", "object_id")]
        indexes = [models.Index(fields=["on_hand", "reserved"], name="inv_item_levels_idx")]

    def __str__(self):
        return f"Inventory({self.sku or self.object_id}): {self.available} available"

    # ── read helpers ──────────────────────────────────────────────────────────
    @property
    def available(self) -> int:
        return max(0, self.on_hand - self.reserved)

    @property
    def low_stock_threshold(self) -> int:
        if self.reorder_point is not None:
            return self.reorder_point
        return int(fc_setting("LOW_STOCK_THRESHOLD", 5))

    @property
    def is_low(self) -> bool:
        return self.available <= self.low_stock_threshold

    def oversell_allowed(self) -> bool:
        return self.allow_oversell or bool(fc_setting("ALLOW_OVERSELL", False))

    def is_available(self, quantity: int = 1) -> bool:
        return self.oversell_allowed() or self.available >= quantity

    @classmethod
    def get_for_product(cls, product, create=True) -> "InventoryItem":
        ct = ContentType.objects.get_for_model(product)
        if not create:
            return cls.objects.filter(content_type=ct, object_id=product.pk).first()
        item, _ = cls.objects.get_or_create(
            content_type=ct,
            object_id=product.pk,
            defaults={"sku": str(getattr(product, "sku", "") or "")[:100]},
        )
        return item

    # ── atomic mutations ─────────────────────────────────────────────────────
    def _after_change(self, before_available, movement_type=None, quantity=0, reference="", note=""):
        """Refresh, record the movement and emit threshold-crossing events."""
        from . import signals

        self.refresh_from_db(fields=["on_hand", "reserved", "sold", "updated_at"])
        if movement_type:
            StockMovement.objects.create(
                inventory_item=self,
                movement_type=movement_type,
                quantity=quantity,
                reference=reference,
                note=note,
            )
        signals.emit_stock_events(self, before_available)

    def reserve(self, quantity: int, order_ref: str = "", ttl_minutes: int = None) -> "StockReservation":
        """Hold ``quantity`` units. Raises ``InsufficientStockError`` atomically."""
        if quantity <= 0:
            raise StockReservationError("Quantity must be positive.")
        with transaction.atomic():
            qs = InventoryItem.objects.filter(pk=self.pk)
            if not self.oversell_allowed():
                qs = qs.filter(Q(allow_oversell=True) | Q(on_hand__gte=F("reserved") + quantity))
            before = self.available
            if not qs.update(reserved=F("reserved") + quantity, updated_at=timezone.now()):
                self.refresh_from_db(fields=["on_hand", "reserved"])
                raise InsufficientStockError(available=self.available, requested=quantity, extra={"sku": self.sku})
            ttl = inventory_setting("STOCK_RESERVATION_MINUTES") if ttl_minutes is None else ttl_minutes
            reservation = StockReservation.objects.create(
                inventory_item=self,
                quantity=quantity,
                order_ref=order_ref,
                expires_at=timezone.now() + timedelta(minutes=ttl) if ttl else None,
            )
            self._after_change(before)
        return reservation

    def release(self, quantity: int = None, reservation_id=None) -> bool:
        """Release a pending reservation (idempotent). Returns True if released."""
        with transaction.atomic():
            if reservation_id is not None:
                res = StockReservation.objects.filter(pk=reservation_id, inventory_item=self).first()
                if res is None:
                    return False
                claimed = StockReservation.objects.filter(pk=res.pk, status=StockReservation.STATUS_PENDING).update(
                    status=StockReservation.STATUS_RELEASED, updated_at=timezone.now()
                )
                if not claimed:
                    return False
                quantity = res.quantity
            before = self.available
            InventoryItem.objects.filter(pk=self.pk).update(
                reserved=Greatest(F("reserved") - quantity, 0), updated_at=timezone.now()
            )
            self._after_change(before)
        return True

    def confirm_sale(self, quantity: int = None, reservation_id=None, reference: str = "") -> bool:
        """
        Convert a reservation into a sale (deduct on-hand). Idempotent per
        reservation. If the reservation already expired, stock is still deducted:
        the customer paid, so the sale stands.
        """
        with transaction.atomic():
            releases_reserved = True
            if reservation_id is not None:
                res = StockReservation.objects.filter(pk=reservation_id, inventory_item=self).first()
                if res is None:
                    return False
                if res.status == StockReservation.STATUS_FULFILLED:
                    return False
                releases_reserved = res.status == StockReservation.STATUS_PENDING
                claimed = StockReservation.objects.filter(
                    pk=res.pk,
                    status__in=[StockReservation.STATUS_PENDING, StockReservation.STATUS_RELEASED],
                ).update(status=StockReservation.STATUS_FULFILLED, updated_at=timezone.now())
                if not claimed:
                    return False
                quantity = res.quantity
                reference = reference or res.order_ref
            before = self.available
            update = {
                "on_hand": Greatest(F("on_hand") - quantity, 0),
                "sold": F("sold") + quantity,
                "updated_at": timezone.now(),
            }
            if releases_reserved:
                update["reserved"] = Greatest(F("reserved") - quantity, 0)
            InventoryItem.objects.filter(pk=self.pk).update(**update)
            self._after_change(before, StockMovement.TYPE_SALE, -quantity, reference)
        return True

    def restock(self, quantity: int, note: str = "", reference: str = ""):
        return self._add(quantity, StockMovement.TYPE_RESTOCK, note, reference)

    def return_stock(self, quantity: int, note: str = "", reference: str = ""):
        return self._add(quantity, StockMovement.TYPE_RETURN, note, reference)

    def _add(self, quantity, movement_type, note, reference):
        if quantity <= 0:
            raise StockReservationError("Quantity must be positive.")
        with transaction.atomic():
            before = self.available
            InventoryItem.objects.filter(pk=self.pk).update(on_hand=F("on_hand") + quantity, updated_at=timezone.now())
            self._after_change(before, movement_type, quantity, reference, note)
        return self

    def adjust(self, new_on_hand: int, note: str = "", reference: str = ""):
        """Set on-hand to a counted value (stock take)."""
        if new_on_hand < 0:
            raise StockReservationError("On-hand cannot be negative.")
        with transaction.atomic():
            locked = InventoryItem.objects.select_for_update().get(pk=self.pk)
            delta = new_on_hand - locked.on_hand
            before = locked.available
            InventoryItem.objects.filter(pk=self.pk).update(on_hand=new_on_hand, updated_at=timezone.now())
            self._after_change(before, StockMovement.TYPE_ADJUSTMENT, delta, reference, note)
        return self


class StockReservation(TimeStampedUUIDModel):
    STATUS_PENDING = "pending"
    STATUS_FULFILLED = "fulfilled"
    STATUS_RELEASED = "released"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_FULFILLED, _("Fulfilled")),
        (STATUS_RELEASED, _("Released")),
    ]

    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="reservations",
        verbose_name=_("inventory item"),
    )
    quantity = models.PositiveIntegerField(_("quantity"))
    order_ref = models.CharField(_("order reference"), max_length=100, blank=True, db_index=True)
    status = models.CharField(_("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)

    class Meta:
        verbose_name = _("stock reservation")
        verbose_name_plural = _("stock reservations")
        indexes = [models.Index(fields=["status", "expires_at"], name="inv_res_status_exp_idx")]

    def __str__(self):
        return f"Reservation({self.inventory_item_id}, qty={self.quantity}, {self.status})"


class StockMovement(TimeStampedUUIDModel):
    """Append-only audit trail for every stock change."""

    TYPE_RESTOCK = "restock"
    TYPE_SALE = "sale"
    TYPE_RETURN = "return"
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_CHOICES = [
        (TYPE_RESTOCK, _("Restock")),
        (TYPE_SALE, _("Sale")),
        (TYPE_RETURN, _("Return")),
        (TYPE_ADJUSTMENT, _("Adjustment")),
    ]

    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField(_("type"), max_length=20, choices=TYPE_CHOICES)
    quantity = models.IntegerField(_("quantity"), help_text=_("Positive adds stock, negative removes it."))
    note = models.TextField(_("note"), blank=True)
    reference = models.CharField(_("reference"), max_length=100, blank=True)

    class Meta:
        verbose_name = _("stock movement")
        verbose_name_plural = _("stock movements")
        ordering = ["-created_at"]

    def __str__(self):
        return f"StockMovement({self.movement_type}, qty={self.quantity})"


class StockAlert(TimeStampedUUIDModel):
    """'Notify me when back in stock' subscription."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField(_("product ID"))
    product = GenericForeignKey("content_type", "object_id")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="stock_alerts",
    )
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("phone"), max_length=20, blank=True)
    notified_at = models.DateTimeField(_("notified at"), null=True, blank=True)

    class Meta:
        verbose_name = _("back-in-stock alert")
        verbose_name_plural = _("back-in-stock alerts")
        indexes = [models.Index(fields=["content_type", "object_id", "notified_at"], name="inv_alert_pending_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "user"],
                condition=Q(notified_at__isnull=True, user__isnull=False),
                name="inv_alert_unique_user_pending",
            ),
            models.UniqueConstraint(
                fields=["content_type", "object_id", "email"],
                condition=Q(notified_at__isnull=True, user__isnull=True),
                name="inv_alert_unique_email_pending",
            ),
        ]

    def __str__(self):
        return f"StockAlert({self.object_id} → {self.user or self.email})"

    @property
    def recipient_email(self):
        return self.email or getattr(self.user, "email", "")
