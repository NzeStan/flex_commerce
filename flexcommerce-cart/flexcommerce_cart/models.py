"""
FlexCommerce Cart.

Carts belong to a signed-in user, or to an anonymous visitor identified by a
secret ``token`` (sent as the ``X-Cart-Token`` header by mobile/SPA clients, or
kept in the Django session for browser clients).

Totals are denormalised on the cart and recomputed by
``services.CartPricingService`` after every change, so reading a cart is cheap.
All money amounts are VAT-inclusive (gross) unless named ``*_net``/``*_vat``.
"""

import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel

from .conf import cart_setting

ZERO = Decimal("0.00")


def new_cart_token():
    return secrets.token_urlsafe(32)


class Cart(TimeStampedUUIDModel):
    STATUS_ACTIVE = "active"
    STATUS_LOCKED = "locked"
    STATUS_ORDERED = "ordered"
    STATUS_MERGED = "merged"
    STATUS_ABANDONED = "abandoned"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, _("Active")),
        (STATUS_LOCKED, _("Locked")),
        (STATUS_ORDERED, _("Ordered")),
        (STATUS_MERGED, _("Merged")),
        (STATUS_ABANDONED, _("Abandoned")),
        (STATUS_EXPIRED, _("Expired")),
    ]

    session_key = models.CharField(_("session key"), max_length=40, blank=True, db_index=True)
    token = models.CharField(_("cart token"), max_length=64, unique=True, default=new_cart_token, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="carts",
        verbose_name=_("user"),
    )
    status = models.CharField(_("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    currency = models.CharField(_("currency"), max_length=10, default="NGN")
    email = models.EmailField(_("contact email"), blank=True, help_text=_("Guest contact for cart recovery."))
    phone = models.CharField(_("contact phone"), max_length=20, blank=True)

    coupon_code = models.CharField(_("coupon code"), max_length=100, blank=True)
    coupon_error = models.CharField(_("coupon message"), max_length=255, blank=True)
    discount_amount = models.DecimalField(_("discount amount"), max_digits=14, decimal_places=2, default=ZERO)
    free_shipping = models.BooleanField(_("free shipping"), default=False)
    shipping_method_id = models.UUIDField(_("shipping method ID"), null=True, blank=True)

    # Denormalised totals (VAT-inclusive).
    subtotal_amount = models.DecimalField(_("subtotal"), max_digits=14, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(_("VAT included"), max_digits=14, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(_("total"), max_digits=14, decimal_places=2, default=ZERO)
    items_count = models.PositiveIntegerField(_("units in cart"), default=0)

    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    last_activity = models.DateTimeField(_("last activity"), auto_now=True, db_index=True)
    abandoned_notified_at = models.DateTimeField(_("abandonment notified at"), null=True, blank=True)

    class Meta:
        verbose_name = _("cart")
        verbose_name_plural = _("carts")
        indexes = [
            models.Index(fields=["session_key", "status"], name="cart_session_status_idx"),
            models.Index(fields=["user", "status"], name="cart_user_status_idx"),
            models.Index(fields=["status", "last_activity"], name="cart_status_activity_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session_key"],
                condition=models.Q(status="active", user__isnull=True) & ~models.Q(session_key=""),
                name="unique_active_session_cart",
            ),
            models.UniqueConstraint(
                fields=["user"], condition=models.Q(status="active"), name="unique_active_user_cart"
            ),
        ]

    def __str__(self):
        owner = self.user or (self.session_key or self.token)[:8]
        return f"Cart({owner}) [{self.status}]"

    # ── state ────────────────────────────────────────────────────────────────
    @property
    def is_active(self):
        return self.status == self.STATUS_ACTIVE

    @property
    def is_locked(self):
        return self.status == self.STATUS_LOCKED

    @property
    def is_expired(self):
        return bool(self.expires_at and timezone.now() > self.expires_at)

    def _set_status(self, status):
        self.status = status
        self.save(update_fields=["status", "updated_at"])

    def lock(self):
        self._set_status(self.STATUS_LOCKED)

    def unlock(self):
        self._set_status(self.STATUS_ACTIVE)

    def mark_ordered(self):
        self._set_status(self.STATUS_ORDERED)

    def mark_abandoned(self):
        self._set_status(self.STATUS_ABANDONED)

    def mark_expired(self):
        self._set_status(self.STATUS_EXPIRED)

    def set_expiry(self, save=True):
        self.expires_at = timezone.now() + timedelta(days=cart_setting("CART_EXPIRY_DAYS"))
        if save:
            self.save(update_fields=["expires_at", "updated_at"])

    # ── totals (compatible names) ───────────────────────────────────────
    @property
    def item_count(self):
        return self.items_count

    @property
    def subtotal(self):
        return self.subtotal_amount

    @property
    def tax_total(self):
        return self.tax_amount

    @property
    def total(self):
        return self.total_amount

    def recalculate(self):
        from .services import CartPricingService

        CartPricingService(self).recalculate()


class CartItem(TimeStampedUUIDModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items", verbose_name=_("cart"))
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, verbose_name=_("product type"))
    object_id = models.UUIDField(_("product ID"))
    product = GenericForeignKey("content_type", "object_id")
    quantity = models.PositiveIntegerField(_("quantity"), default=1)

    # Price snapshot refreshed on every recalculation.
    unit_price = models.DecimalField(_("unit price"), max_digits=14, decimal_places=2, default=ZERO)
    unit_price_with_tax = models.DecimalField(_("unit price with tax"), max_digits=14, decimal_places=2, default=ZERO)
    unit_net = models.DecimalField(_("unit net"), max_digits=14, decimal_places=2, default=ZERO)
    vat_rate = models.DecimalField(_("VAT rate"), max_digits=5, decimal_places=4, default=Decimal("0.0750"))
    vat_amount = models.DecimalField(_("unit VAT"), max_digits=14, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(_("line discount"), max_digits=14, decimal_places=2, default=ZERO)
    is_saved_for_later = models.BooleanField(_("saved for later"), default=False)

    class Meta:
        verbose_name = _("cart item")
        verbose_name_plural = _("cart items")
        ordering = ["created_at"]
        unique_together = [("cart", "content_type", "object_id")]
        indexes = [models.Index(fields=["content_type", "object_id"], name="cart_item_product_idx")]

    def __str__(self):
        return f"CartItem({self.object_id} × {self.quantity})"

    @property
    def line_total(self):
        """Gross line total before the coupon discount."""
        return self.unit_price_with_tax * self.quantity

    @property
    def line_total_with_tax(self):
        return self.unit_price_with_tax * self.quantity

    @property
    def line_vat(self):
        return self.vat_amount * self.quantity

    @property
    def line_net(self):
        return self.unit_net * self.quantity


class SavedItem(TimeStampedUUIDModel):
    """'Save for later' list of a signed-in user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_items",
        verbose_name=_("user"),
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField(_("product ID"))
    product = GenericForeignKey("content_type", "object_id")
    saved_price = models.DecimalField(_("saved price"), max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = _("saved item")
        verbose_name_plural = _("saved items")
        ordering = ["-created_at"]
        unique_together = [("user", "content_type", "object_id")]
        indexes = [models.Index(fields=["content_type", "object_id"], name="cart_saved_product_idx")]

    def __str__(self):
        return f"SavedItem({self.user} → {self.object_id})"
