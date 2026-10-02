"""
FlexCommerce Orders.

Full lifecycle: placement → payment → fulfilment (partial shipments with tracking
events) → delivery → returns → refunds, with a customer-visible timeline and an
internal audit log. State changes go through ``services.OrderService``.
"""

import secrets
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel
from flexcommerce_core.utils.helpers import StateMachine

ZERO = Decimal("0.00")


def new_access_token():
    return secrets.token_urlsafe(24)


class Order(TimeStampedUUIDModel):
    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_PROCESSING = "processing"
    STATUS_PARTIALLY_SHIPPED = "partially_shipped"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUNDED = "refunded"
    STATUS_PARTIALLY_REFUNDED = "partially_refunded"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_CONFIRMED, _("Confirmed")),
        (STATUS_PROCESSING, _("Processing")),
        (STATUS_PARTIALLY_SHIPPED, _("Partially shipped")),
        (STATUS_SHIPPED, _("Shipped")),
        (STATUS_DELIVERED, _("Delivered")),
        (STATUS_CANCELLED, _("Cancelled")),
        (STATUS_REFUNDED, _("Refunded")),
        (STATUS_PARTIALLY_REFUNDED, _("Partially refunded")),
    ]

    PAYMENT_PENDING = "pending"
    PAYMENT_PAID = "paid"
    PAYMENT_FAILED = "failed"
    PAYMENT_PARTIALLY_REFUNDED = "partially_refunded"
    PAYMENT_REFUNDED = "refunded"
    PAYMENT_CHOICES = [
        (PAYMENT_PENDING, _("Pending")),
        (PAYMENT_PAID, _("Paid")),
        (PAYMENT_FAILED, _("Failed")),
        (PAYMENT_PARTIALLY_REFUNDED, _("Partially refunded")),
        (PAYMENT_REFUNDED, _("Refunded")),
    ]

    PAYMENT_METHOD_CARD = "card"
    PAYMENT_METHOD_BANK = "bank_transfer"
    PAYMENT_METHOD_WALLET = "wallet"
    PAYMENT_METHOD_POD = "pay_on_delivery"
    PAYMENT_METHOD_CHOICES = [
        (PAYMENT_METHOD_CARD, _("Card / online")),
        (PAYMENT_METHOD_BANK, _("Bank Transfer")),
        (PAYMENT_METHOD_WALLET, _("Wallet")),
        (PAYMENT_METHOD_POD, _("Pay on Delivery")),
    ]

    # Reference & customer
    order_number = models.CharField(_("order number"), max_length=50, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("user"),
    )
    email = models.EmailField(_("email"), blank=True, db_index=True)
    phone = models.CharField(_("phone"), max_length=20, blank=True)
    session_key = models.CharField(_("session key"), max_length=40, blank=True)
    cart_id = models.UUIDField(_("cart"), null=True, blank=True)
    idempotency_key = models.CharField(_("idempotency key"), max_length=100, blank=True)
    access_token = models.CharField(_("guest access token"), max_length=64, default=new_access_token, editable=False)

    # Addresses (snapshots)
    shipping_address = models.JSONField(_("shipping address"), default=dict)
    billing_address = models.JSONField(_("billing address"), default=dict)

    # Money (VAT-inclusive unless noted)
    subtotal = models.DecimalField(_("items subtotal"), max_digits=14, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(_("discount"), max_digits=14, decimal_places=2, default=ZERO)
    shipping_cost = models.DecimalField(_("shipping"), max_digits=14, decimal_places=2, default=ZERO)
    shipping_vat = models.DecimalField(_("shipping VAT"), max_digits=14, decimal_places=2, default=ZERO)
    tax_total = models.DecimalField(_("VAT total"), max_digits=14, decimal_places=2, default=ZERO)
    grand_total = models.DecimalField(_("grand total"), max_digits=14, decimal_places=2, default=ZERO)
    amount_paid = models.DecimalField(_("amount paid"), max_digits=14, decimal_places=2, default=ZERO)
    amount_refunded = models.DecimalField(_("amount refunded"), max_digits=14, decimal_places=2, default=ZERO)
    currency = models.CharField(_("currency"), max_length=10, default="NGN")
    coupon_code = models.CharField(_("coupon code"), max_length=100, blank=True)

    # Status
    status = models.CharField(_("status"), max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    payment_status = models.CharField(
        _("payment status"),
        max_length=20,
        choices=PAYMENT_CHOICES,
        default=PAYMENT_PENDING,
        db_index=True,
    )
    payment_method = models.CharField(_("payment method"), max_length=30, blank=True)
    payment_provider = models.CharField(_("payment provider"), max_length=50, blank=True)
    payment_reference = models.CharField(_("payment reference"), max_length=200, blank=True, db_index=True)

    # Fulfilment
    shipping_method_id = models.UUIDField(_("shipping method"), null=True, blank=True)
    shipping_method_name = models.CharField(_("shipping method name"), max_length=200, blank=True)
    pickup_station = models.JSONField(_("pickup station"), default=dict, blank=True)
    estimated_delivery_from = models.DateField(_("estimated delivery from"), null=True, blank=True)
    estimated_delivery_to = models.DateField(_("estimated delivery to"), null=True, blank=True)
    vendor_id = models.UUIDField(_("vendor ID"), null=True, blank=True)

    # Notes
    customer_note = models.TextField(_("customer note"), blank=True)
    internal_note = models.TextField(_("internal note"), blank=True)
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=255, blank=True)

    # Lifecycle timestamps
    confirmed_at = models.DateTimeField(_("confirmed at"), null=True, blank=True)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    shipped_at = models.DateTimeField(_("shipped at"), null=True, blank=True)
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    payment_due_at = models.DateTimeField(_("payment due"), null=True, blank=True)

    class Meta:
        verbose_name = _("order")
        verbose_name_plural = _("orders")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"], name="orders_user_status_idx"),
            models.Index(fields=["status", "payment_status"], name="orders_status_pay_idx"),
            models.Index(fields=["payment_status", "payment_due_at"], name="orders_payment_due_idx"),
            models.Index(fields=["created_at"], name="orders_created_idx"),
            models.Index(fields=["confirmed_at"], name="orders_confirmed_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="orders_unique_idempotency_key",
            )
        ]

    def __str__(self):
        return f"Order #{self.order_number} [{self.status}]"

    @property
    def customer_email(self):
        return self.email or (self.user.email if self.user_id else "")

    @property
    def customer_name(self):
        if self.user_id:
            full = self.user.get_full_name() if hasattr(self.user, "get_full_name") else ""
            if full:
                return full
        addr = self.shipping_address or {}
        name = f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip()
        return name or "Customer"

    @property
    def balance_due(self):
        return max(ZERO, self.grand_total - self.amount_paid)

    @property
    def refundable_amount(self):
        return max(ZERO, self.amount_paid - self.amount_refunded)

    @property
    def is_paid(self):
        return self.payment_status in (self.PAYMENT_PAID, self.PAYMENT_PARTIALLY_REFUNDED)

    def get_state_machine(self) -> "OrderStateMachine":
        return OrderStateMachine(self)

    def transition(self, to_state: str, actor="system", save=True, note=""):
        """Change status with side effects (stock, coupons, events). See OrderService."""
        from .services import OrderService

        return OrderService(self).transition(to_state, actor=actor, note=note)

    @classmethod
    def generate_order_number(cls) -> str:
        from .services import generate_order_number

        return generate_order_number()


class OrderStateMachine(StateMachine):
    TRANSITIONS = {
        Order.STATUS_PENDING: [Order.STATUS_CONFIRMED, Order.STATUS_CANCELLED],
        Order.STATUS_CONFIRMED: [
            Order.STATUS_PROCESSING,
            Order.STATUS_PARTIALLY_SHIPPED,
            Order.STATUS_SHIPPED,
            Order.STATUS_CANCELLED,
        ],
        Order.STATUS_PROCESSING: [
            Order.STATUS_PARTIALLY_SHIPPED,
            Order.STATUS_SHIPPED,
            Order.STATUS_CANCELLED,
        ],
        Order.STATUS_PARTIALLY_SHIPPED: [Order.STATUS_SHIPPED, Order.STATUS_DELIVERED],
        Order.STATUS_SHIPPED: [Order.STATUS_DELIVERED],
        Order.STATUS_DELIVERED: [Order.STATUS_PARTIALLY_REFUNDED, Order.STATUS_REFUNDED],
        Order.STATUS_PARTIALLY_REFUNDED: [Order.STATUS_REFUNDED],
        Order.STATUS_CANCELLED: [],
        Order.STATUS_REFUNDED: [],
    }


class OrderItem(TimeStampedUUIDModel):
    """Immutable snapshot of a purchased line."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name=_("order"))
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True)
    object_id = models.UUIDField(_("product ID"), null=True, blank=True, db_index=True)
    product = GenericForeignKey("content_type", "object_id")
    parent_product_id = models.UUIDField(_("parent product ID"), null=True, blank=True)

    product_name = models.CharField(_("product name"), max_length=255)
    product_sku = models.CharField(_("SKU"), max_length=100, blank=True)
    product_data = models.JSONField(_("product snapshot"), default=dict, blank=True)

    quantity = models.PositiveIntegerField(_("quantity"), default=1)
    unit_price_net = models.DecimalField(_("unit price (net)"), max_digits=14, decimal_places=2)
    unit_vat = models.DecimalField(_("unit VAT"), max_digits=14, decimal_places=2, default=ZERO)
    unit_price_gross = models.DecimalField(_("unit price (gross)"), max_digits=14, decimal_places=2)
    vat_rate = models.DecimalField(_("VAT rate"), max_digits=5, decimal_places=4, default=Decimal("0.0750"))
    line_total = models.DecimalField(_("line total (gross)"), max_digits=14, decimal_places=2)
    line_vat = models.DecimalField(_("line VAT"), max_digits=14, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(_("line discount"), max_digits=14, decimal_places=2, default=ZERO)

    quantity_shipped = models.PositiveIntegerField(_("quantity shipped"), default=0)
    quantity_returned = models.PositiveIntegerField(_("quantity returned"), default=0)

    # Marketplace
    vendor_id = models.UUIDField(_("vendor ID"), null=True, blank=True, db_index=True)
    commission_rate = models.DecimalField(_("commission rate"), max_digits=5, decimal_places=4, default=ZERO)

    class Meta:
        verbose_name = _("order item")
        verbose_name_plural = _("order items")
        ordering = ["created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"], name="orders_item_product_idx")]

    def __str__(self):
        return f"{self.product_name} × {self.quantity}"

    @property
    def paid_line_total(self):
        """What the customer actually paid for this line (after discount)."""
        return max(ZERO, self.line_total - self.discount_amount)

    @property
    def unit_paid(self):
        return (self.paid_line_total / self.quantity) if self.quantity else ZERO


class OrderEvent(TimeStampedUUIDModel):
    """Customer-visible order timeline ("Order placed", "Shipped with GIG", ...)."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    event = models.CharField(_("event"), max_length=50)
    message = models.CharField(_("message"), max_length=500)
    is_customer_visible = models.BooleanField(_("visible to customer"), default=True)
    actor = models.CharField(_("actor"), max_length=255, blank=True)
    data = models.JSONField(_("data"), default=dict, blank=True)

    class Meta:
        verbose_name = _("order event")
        verbose_name_plural = _("order events")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.order.order_number}: {self.message}"


class Shipment(TimeStampedUUIDModel):
    STATUS_PENDING = "pending"
    STATUS_DISPATCHED = "dispatched"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
    STATUS_DELIVERED = "delivered"
    STATUS_FAILED = "failed"
    STATUS_RETURNED = "returned"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_DISPATCHED, _("Dispatched")),
        (STATUS_IN_TRANSIT, _("In transit")),
        (STATUS_OUT_FOR_DELIVERY, _("Out for delivery")),
        (STATUS_DELIVERED, _("Delivered")),
        (STATUS_FAILED, _("Delivery failed")),
        (STATUS_RETURNED, _("Returned to sender")),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="shipments")
    carrier = models.CharField(_("carrier"), max_length=100, blank=True)
    tracking_number = models.CharField(_("tracking number"), max_length=200, blank=True, db_index=True)
    tracking_url = models.URLField(_("tracking URL"), max_length=500, blank=True)
    status = models.CharField(_("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_DISPATCHED)
    shipped_at = models.DateTimeField(_("shipped at"), null=True, blank=True)
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True)
    estimated_delivery = models.DateField(_("estimated delivery"), null=True, blank=True)
    items = models.JSONField(_("items"), default=list, help_text=_('[{"order_item_id": "...", "quantity": 1}]'))
    vendor_id = models.UUIDField(_("vendor ID"), null=True, blank=True)

    class Meta:
        verbose_name = _("shipment")
        verbose_name_plural = _("shipments")
        ordering = ["created_at"]

    def __str__(self):
        return f"Shipment({self.order.order_number}, {self.status})"


class ShipmentEvent(TimeStampedUUIDModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(_("status"), max_length=20, choices=Shipment.STATUS_CHOICES)
    description = models.CharField(_("description"), max_length=500, blank=True)
    location = models.CharField(_("location"), max_length=255, blank=True)
    occurred_at = models.DateTimeField(_("occurred at"))

    class Meta:
        verbose_name = _("shipment event")
        verbose_name_plural = _("shipment events")
        ordering = ["occurred_at"]

    def __str__(self):
        return f"{self.shipment_id}: {self.status}"


class Refund(TimeStampedUUIDModel):
    STATUS_REQUESTED = "requested"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_REQUESTED, _("Requested")),
        (STATUS_APPROVED, _("Approved")),
        (STATUS_REJECTED, _("Rejected")),
        (STATUS_PROCESSED, _("Processed")),
        (STATUS_FAILED, _("Failed")),
    ]
    METHOD_ORIGINAL = "original"
    METHOD_WALLET = "wallet"
    METHOD_MANUAL = "manual"
    METHOD_CHOICES = [
        (METHOD_ORIGINAL, _("Original payment method")),
        (METHOD_WALLET, _("Store wallet / credit")),
        (METHOD_MANUAL, _("Manual (bank transfer / cash)")),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="refunds")
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    reason = models.TextField(_("reason"))
    method = models.CharField(_("method"), max_length=20, choices=METHOD_CHOICES, default=METHOD_ORIGINAL)
    status = models.CharField(_("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_REQUESTED)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_refunds",
    )
    processed_at = models.DateTimeField(_("processed at"), null=True, blank=True)
    reference = models.CharField(_("reference"), max_length=200, blank=True)
    failure_reason = models.CharField(_("failure reason"), max_length=500, blank=True)
    items = models.JSONField(_("items"), default=list, blank=True)
    return_request = models.ForeignKey(
        "ReturnRequest", on_delete=models.SET_NULL, null=True, blank=True, related_name="refunds"
    )

    class Meta:
        verbose_name = _("refund")
        verbose_name_plural = _("refunds")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refund({self.order.order_number}, {self.amount} {self.order.currency}, {self.status})"


class ReturnRequest(TimeStampedUUIDModel):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_RECEIVED = "received"
    STATUS_REFUNDED = "refunded"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending review")),
        (STATUS_APPROVED, _("Approved — awaiting item")),
        (STATUS_REJECTED, _("Rejected")),
        (STATUS_RECEIVED, _("Item received")),
        (STATUS_REFUNDED, _("Refunded")),
    ]
    REASON_CHOICES = [
        ("damaged", _("Damaged or defective")),
        ("wrong_item", _("Wrong item delivered")),
        ("not_as_described", _("Not as described")),
        ("missing_parts", _("Missing parts or accessories")),
        ("changed_mind", _("Changed my mind")),
        ("other", _("Other")),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="returns")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_requests",
    )
    reason_code = models.CharField(_("reason"), max_length=30, choices=REASON_CHOICES, default="other")
    reason = models.TextField(_("details"), blank=True)
    status = models.CharField(_("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    items = models.JSONField(_("items"), default=list, help_text=_('[{"order_item_id": "...", "quantity": 1}]'))
    image_urls = models.JSONField(_("photos"), default=list, blank=True)
    refund_method = models.CharField(
        _("refund method"),
        max_length=20,
        choices=Refund.METHOD_CHOICES,
        default=Refund.METHOD_ORIGINAL,
    )
    staff_note = models.TextField(_("staff note"), blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_returns",
    )
    received_at = models.DateTimeField(_("received at"), null=True, blank=True)

    class Meta:
        verbose_name = _("return request")
        verbose_name_plural = _("return requests")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Return({self.order.order_number}, {self.status})"
