"""
FlexCommerce Analytics.

* ``AnalyticsEvent``      – append-only event stream recorded from domain signals
* ``DailyRevenueSummary`` – pre-aggregated daily numbers (per currency) for fast dashboards

Reports (``flexcommerce_analytics.reports``) query the order tables directly and
are exposed as admin-only JSON and CSV endpoints.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel

ZERO = Decimal("0.00")


class AnalyticsEvent(TimeStampedUUIDModel):
    EVENT_ORDER_CREATED = "order.created"
    EVENT_ORDER_CONFIRMED = "order.confirmed"
    EVENT_ORDER_PAID = "order.paid"
    EVENT_ORDER_CANCELLED = "order.cancelled"
    EVENT_ORDER_REFUNDED = "order.refunded"
    EVENT_REFUND_PROCESSED = "refund.processed"
    EVENT_CART_ABANDONED = "cart.abandoned"
    EVENT_CART_RECOVERED = "cart.recovered"
    EVENT_PRODUCT_VIEWED = "product.viewed"
    EVENT_COUPON_USED = "coupon.used"
    EVENT_REVIEW_SUBMITTED = "review.submitted"
    EVENT_CHOICES = [
        (EVENT_ORDER_CREATED, _("Order Created")),
        (EVENT_ORDER_CONFIRMED, _("Order Confirmed")),
        (EVENT_ORDER_PAID, _("Order Paid")),
        (EVENT_ORDER_CANCELLED, _("Order Cancelled")),
        (EVENT_ORDER_REFUNDED, _("Order Refunded")),
        (EVENT_REFUND_PROCESSED, _("Refund Processed")),
        (EVENT_CART_ABANDONED, _("Cart Abandoned")),
        (EVENT_CART_RECOVERED, _("Cart Recovered")),
        (EVENT_PRODUCT_VIEWED, _("Product Viewed")),
        (EVENT_COUPON_USED, _("Coupon Used")),
        (EVENT_REVIEW_SUBMITTED, _("Review Submitted")),
    ]

    event_type = models.CharField(_("event type"), max_length=50, choices=EVENT_CHOICES, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_events",
    )
    session_key = models.CharField(_("session key"), max_length=40, blank=True)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2, default=ZERO)
    currency = models.CharField(_("currency"), max_length=10, default="NGN")
    vendor_id = models.UUIDField(_("vendor ID"), null=True, blank=True, db_index=True)
    reference = models.CharField(_("reference"), max_length=100, blank=True, db_index=True)
    meta = models.JSONField(_("meta"), default=dict, blank=True)

    class Meta:
        verbose_name = _("analytics event")
        verbose_name_plural = _("analytics events")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "created_at"], name="analytics_type_created_idx"),
            models.Index(fields=["user", "event_type"], name="analytics_user_type_idx"),
            models.Index(fields=["vendor_id", "event_type"], name="analytics_vendor_type_idx"),
        ]

    def __str__(self):
        return f"AnalyticsEvent({self.event_type}, ref={self.reference or '-'})"


class DailyRevenueSummary(TimeStampedUUIDModel):
    date = models.DateField(_("date"), db_index=True)
    currency = models.CharField(_("currency"), max_length=10, default="NGN")
    order_count = models.PositiveIntegerField(_("orders"), default=0)
    units_sold = models.PositiveIntegerField(_("units sold"), default=0)
    gross_revenue = models.DecimalField(_("gross sales"), max_digits=16, decimal_places=2, default=ZERO)
    net_revenue = models.DecimalField(_("net sales (ex VAT)"), max_digits=16, decimal_places=2, default=ZERO)
    tax_collected = models.DecimalField(_("VAT"), max_digits=16, decimal_places=2, default=ZERO)
    shipping_collected = models.DecimalField(_("shipping"), max_digits=16, decimal_places=2, default=ZERO)
    discount_given = models.DecimalField(_("discounts"), max_digits=16, decimal_places=2, default=ZERO)
    refund_total = models.DecimalField(_("refunds"), max_digits=16, decimal_places=2, default=ZERO)
    average_order_value = models.DecimalField(_("average order value"), max_digits=16, decimal_places=2, default=ZERO)
    cancelled_order_count = models.PositiveIntegerField(_("cancelled orders"), default=0)
    abandoned_cart_count = models.PositiveIntegerField(_("abandoned carts"), default=0)
    new_customers = models.PositiveIntegerField(_("new customers"), default=0)

    class Meta:
        verbose_name = _("daily revenue summary")
        verbose_name_plural = _("daily revenue summaries")
        ordering = ["-date"]
        unique_together = [("date", "currency")]

    def __str__(self):
        return f"Revenue({self.date}): {self.gross_revenue} {self.currency}"
