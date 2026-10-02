"""
FlexCommerce Discounts.

* ``Coupon`` — percentage (with cap), fixed, free shipping, buy-X-get-Y; product /
  category / vendor restrictions; first-order-only; global and per-customer usage
  limits enforced atomically at checkout; optional automatic (code-less) promos.
* ``FlashSale`` / ``FlashSaleItem`` — time-boxed sale prices with optional
  "only N left" quantity caps, applied through the core ``price.modify`` hook.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel
from flexcommerce_core.utils.vat import round_price

ZERO = Decimal("0.00")


class Coupon(TimeStampedUUIDModel):
    TYPE_PERCENTAGE = "percentage"
    TYPE_FIXED = "fixed"
    TYPE_FREE_SHIPPING = "free_shipping"
    TYPE_BUY_X_GET_Y = "buy_x_get_y"
    TYPE_CHOICES = [
        (TYPE_PERCENTAGE, _("Percentage Discount")),
        (TYPE_FIXED, _("Fixed Amount Discount")),
        (TYPE_FREE_SHIPPING, _("Free Shipping")),
        (TYPE_BUY_X_GET_Y, _("Buy X Get Y")),
    ]

    code = models.CharField(_("code"), max_length=100, unique=True, db_index=True)
    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True)
    coupon_type = models.CharField(_("type"), max_length=20, choices=TYPE_CHOICES, default=TYPE_PERCENTAGE)

    value = models.DecimalField(
        _("value"),
        max_digits=14,
        decimal_places=2,
        default=ZERO,
        help_text=_("Percentage (0-100) or fixed amount."),
    )
    max_discount = models.DecimalField(
        _("maximum discount"),
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Cap for percentage coupons, e.g. 10% off up to ₦5,000."),
    )
    buy_quantity = models.PositiveIntegerField(_("buy quantity"), default=0)
    get_quantity = models.PositiveIntegerField(_("get quantity"), default=0)

    minimum_cart_value = models.DecimalField(_("minimum cart value"), max_digits=14, decimal_places=2, default=ZERO)
    is_stackable = models.BooleanField(_("stackable"), default=False, help_text=_("Reserved for future use."))
    applies_to_all = models.BooleanField(_("applies to all products"), default=True)
    restricted_categories = models.JSONField(
        _("only these categories"),
        default=list,
        blank=True,
        help_text=_("Category ids. Empty = any."),
    )
    restricted_products = models.JSONField(
        _("only these products"), default=list, blank=True, help_text=_("Product ids. Empty = any.")
    )
    excluded_products = models.JSONField(_("excluded products"), default=list, blank=True)
    vendor_id = models.UUIDField(
        _("vendor"),
        null=True,
        blank=True,
        help_text=_("Marketplace: only this vendor's products (vendor-funded)."),
    )
    first_order_only = models.BooleanField(_("first order only"), default=False)
    auto_apply = models.BooleanField(
        _("automatic promotion"),
        default=False,
        help_text=_("Applied without a code when it is the best offer."),
    )

    usage_limit = models.PositiveIntegerField(_("usage limit"), null=True, blank=True)
    per_user_limit = models.PositiveIntegerField(_("per customer limit"), default=1, help_text=_("0 = unlimited."))
    used_count = models.PositiveIntegerField(_("used count"), default=0)

    starts_at = models.DateTimeField(_("starts at"), null=True, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    is_active = models.BooleanField(_("is active"), default=True)

    class Meta:
        verbose_name = _("coupon")
        verbose_name_plural = _("coupons")
        indexes = [
            models.Index(fields=["code", "is_active"], name="disc_coupon_code_idx"),
            models.Index(fields=["expires_at"], name="disc_coupon_exp_idx"),
            models.Index(fields=["auto_apply", "is_active"], name="disc_coupon_auto_idx"),
        ]

    def __str__(self):
        return f"{self.code} ({self.coupon_type}: {self.value})"

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def clean(self):
        if self.coupon_type == self.TYPE_PERCENTAGE and not (0 < self.value <= 100):
            raise ValidationError({"value": _("Percentage must be between 0 and 100.")})
        if self.coupon_type == self.TYPE_FIXED and self.value <= 0:
            raise ValidationError({"value": _("Amount must be positive.")})
        if self.coupon_type == self.TYPE_BUY_X_GET_Y and (self.buy_quantity < 1 or self.get_quantity < 1):
            raise ValidationError(_("Buy X Get Y needs buy and get quantities of at least 1."))
        if self.starts_at and self.expires_at and self.starts_at >= self.expires_at:
            raise ValidationError({"expires_at": _("Must be after the start date.")})

    @property
    def is_valid(self) -> bool:
        """Active, within its schedule and below its global usage limit."""
        if not self.is_active:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.expires_at and now > self.expires_at:
            return False
        if self.usage_limit is not None and self.used_count >= self.usage_limit:
            return False
        return True

    def calculate_discount(self, cart_total) -> Decimal:
        """Discount on a plain total (ignores product restrictions)."""
        total = Decimal(str(cart_total))
        if self.coupon_type == self.TYPE_PERCENTAGE:
            amount = total * self.value / Decimal("100")
            if self.max_discount is not None:
                amount = min(amount, self.max_discount)
            return round_price(min(amount, total))
        if self.coupon_type == self.TYPE_FIXED:
            return round_price(min(self.value, total))
        return ZERO


class CouponUsage(TimeStampedUUIDModel):
    """One redemption of a coupon by an order."""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="usages")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="coupon_usages",
        null=True,
        blank=True,
    )
    email = models.EmailField(_("email"), blank=True, db_index=True)
    session_key = models.CharField(_("session key"), max_length=40, blank=True)
    order_ref = models.CharField(_("order reference"), max_length=100, blank=True, db_index=True)
    discount_applied = models.DecimalField(_("discount applied"), max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = _("coupon usage")
        verbose_name_plural = _("coupon usages")
        indexes = [models.Index(fields=["coupon", "user"], name="disc_usage_user_idx")]

    def __str__(self):
        return f"CouponUsage({self.coupon.code}, order={self.order_ref})"


class CouponRedemption(TimeStampedUUIDModel):
    """Per-customer redemption counter (lets per-customer limits scale without a global lock)."""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    customer_key = models.CharField(_("customer"), max_length=150, help_text=_('"user:<id>" or "email:<address>"'))
    count = models.PositiveIntegerField(_("times used"), default=0)

    class Meta:
        verbose_name = _("coupon redemption counter")
        verbose_name_plural = _("coupon redemption counters")
        unique_together = [("coupon", "customer_key")]

    def __str__(self):
        return f"{self.coupon.code} × {self.count} ({self.customer_key})"


class FlashSale(TimeStampedUUIDModel):
    """
    Time-boxed sale. Either list ``items`` (per-product sale price / percentage and
    quantity cap) or set ``discount_percentage`` + ``applicable_products`` (empty = all).
    """

    name = models.CharField(_("name"), max_length=200)
    discount_percentage = models.DecimalField(_("discount %"), max_digits=5, decimal_places=2, default=ZERO)
    starts_at = models.DateTimeField(_("starts at"))
    ends_at = models.DateTimeField(_("ends at"))
    is_active = models.BooleanField(_("is active"), default=True)
    applicable_products = models.JSONField(
        _("applicable product IDs"),
        default=list,
        blank=True,
        help_text=_("Used when the sale has no items. Empty = all products."),
    )

    class Meta:
        verbose_name = _("flash sale")
        verbose_name_plural = _("flash sales")
        indexes = [models.Index(fields=["starts_at", "ends_at", "is_active"], name="disc_flash_window_idx")]

    def __str__(self):
        return f"FlashSale: {self.name} ({self.discount_percentage}%)"

    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": _("Must be after the start.")})
        if not (0 <= self.discount_percentage <= 100):
            raise ValidationError({"discount_percentage": _("Must be between 0 and 100.")})

    @property
    def is_running(self) -> bool:
        now = timezone.now()
        return self.is_active and self.starts_at <= now <= self.ends_at

    def apply_to_price(self, price) -> Decimal:
        price = Decimal(str(price))
        return max(ZERO, round_price(price - price * self.discount_percentage / Decimal("100")))


class FlashSaleItem(TimeStampedUUIDModel):
    sale = models.ForeignKey(FlashSale, on_delete=models.CASCADE, related_name="items")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField(_("product ID"))
    product = GenericForeignKey("content_type", "object_id")
    sale_price = models.DecimalField(
        _("sale price"),
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Fixed sale price. Blank = the sale's discount percentage."),
    )
    quantity_limit = models.PositiveIntegerField(_("units available at sale price"), null=True, blank=True)
    sold_quantity = models.PositiveIntegerField(_("units sold"), default=0)

    class Meta:
        verbose_name = _("flash sale item")
        verbose_name_plural = _("flash sale items")
        unique_together = [("sale", "content_type", "object_id")]
        indexes = [models.Index(fields=["content_type", "object_id"], name="disc_flashitem_obj_idx")]

    def __str__(self):
        return f"{self.sale.name}: {self.object_id}"

    @property
    def remaining(self):
        if self.quantity_limit is None:
            return None
        return max(0, self.quantity_limit - self.sold_quantity)

    def price_for(self, price) -> Decimal:
        if self.sale_price is not None:
            return min(Decimal(str(price)), self.sale_price)
        return self.sale.apply_to_price(price)
