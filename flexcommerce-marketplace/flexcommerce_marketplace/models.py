"""
FlexCommerce Marketplace: multi-vendor selling.

* ``Vendor`` / ``VendorMember`` – seller accounts (KYC, payout details, team)
* ``VendorOrder``               – each customer order split per vendor, with commission
* ``Payout``                    – settlements of vendor earnings after the return window
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel

from .conf import marketplace_setting

ZERO = Decimal("0.00")


class Vendor(TimeStampedUUIDModel):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_SUSPENDED = "suspended"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending review")),
        (STATUS_APPROVED, _("Approved")),
        (STATUS_REJECTED, _("Rejected")),
        (STATUS_SUSPENDED, _("Suspended")),
    ]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_vendors")
    name = models.CharField(_("shop name"), max_length=150, unique=True)
    slug = models.SlugField(_("slug"), max_length=180, unique=True, blank=True)
    description = models.TextField(_("description"), blank=True)
    logo_url = models.URLField(_("logo URL"), max_length=500, blank=True)
    banner_url = models.URLField(_("banner URL"), max_length=500, blank=True)
    email = models.EmailField(_("support email"), blank=True)
    phone = models.CharField(_("support phone"), max_length=20, blank=True)
    address = models.JSONField(_("business address"), default=dict, blank=True)
    status = models.CharField(_("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    is_official_store = models.BooleanField(_("official store"), default=False)
    commission_rate = models.DecimalField(
        _("commission rate"),
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=_("e.g. 0.1000 for 10%. Blank = MARKETPLACE_COMMISSION_RATE."),
    )
    # KYC
    business_registration_number = models.CharField(_("CAC / registration number"), max_length=50, blank=True)
    tax_id = models.CharField(_("TIN"), max_length=50, blank=True)
    kyc_documents = models.JSONField(_("KYC documents"), default=list, blank=True, help_text=_("URLs"))
    # Payout details
    bank_name = models.CharField(_("bank name"), max_length=100, blank=True)
    bank_code = models.CharField(_("bank code"), max_length=20, blank=True)
    account_number = models.CharField(_("account number"), max_length=20, blank=True)
    account_name = models.CharField(_("account name"), max_length=150, blank=True)
    paystack_subaccount = models.CharField(_("Paystack subaccount code"), max_length=50, blank=True)

    rating_avg = models.DecimalField(_("rating"), max_digits=3, decimal_places=2, default=ZERO)
    rating_count = models.PositiveIntegerField(_("ratings"), default=0)
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)
    status_reason = models.CharField(_("status reason"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("vendor")
        verbose_name_plural = _("vendors")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:170] or "shop"
            slug, n = base, 2
            while Vendor.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def is_approved(self):
        return self.status == self.STATUS_APPROVED

    @property
    def effective_commission_rate(self) -> Decimal:
        if self.commission_rate is not None:
            return self.commission_rate
        return Decimal(str(marketplace_setting("MARKETPLACE_COMMISSION_RATE")))


class VendorMember(TimeStampedUUIDModel):
    ROLE_OWNER = "owner"
    ROLE_MANAGER = "manager"
    ROLE_STAFF = "staff"
    ROLE_CHOICES = [(ROLE_OWNER, _("Owner")), (ROLE_MANAGER, _("Manager")), (ROLE_STAFF, _("Staff"))]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="members")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vendor_membership")
    role = models.CharField(_("role"), max_length=20, choices=ROLE_CHOICES, default=ROLE_STAFF)

    class Meta:
        verbose_name = _("vendor member")
        verbose_name_plural = _("vendor members")

    def __str__(self):
        return f"{self.user} @ {self.vendor} ({self.role})"


class Payout(TimeStampedUUIDModel):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_PROCESSING, _("Processing")),
        (STATUS_PAID, _("Paid")),
        (STATUS_FAILED, _("Failed")),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="payouts")
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    currency = models.CharField(_("currency"), max_length=10, default="NGN")
    status = models.CharField(_("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    reference = models.CharField(_("reference"), max_length=200, blank=True)
    failure_reason = models.CharField(_("failure reason"), max_length=500, blank=True)
    processed_at = models.DateTimeField(_("processed at"), null=True, blank=True)
    bank_details = models.JSONField(_("bank details snapshot"), default=dict, blank=True)

    class Meta:
        verbose_name = _("payout")
        verbose_name_plural = _("payouts")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payout({self.vendor}, {self.amount} {self.currency}, {self.status})"


class VendorOrder(TimeStampedUUIDModel):
    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Awaiting payment")),
        (STATUS_CONFIRMED, _("To fulfil")),
        (STATUS_SHIPPED, _("Shipped")),
        (STATUS_DELIVERED, _("Delivered")),
        (STATUS_CANCELLED, _("Cancelled")),
    ]

    order = models.ForeignKey("flexcommerce_orders.Order", on_delete=models.CASCADE, related_name="vendor_orders")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="vendor_orders")
    status = models.CharField(_("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    gross_amount = models.DecimalField(_("sales (after discounts)"), max_digits=14, decimal_places=2, default=ZERO)
    commission_rate = models.DecimalField(_("commission rate"), max_digits=5, decimal_places=4, default=ZERO)
    commission_amount = models.DecimalField(_("commission"), max_digits=14, decimal_places=2, default=ZERO)
    refunded_amount = models.DecimalField(_("refunded"), max_digits=14, decimal_places=2, default=ZERO)
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True)
    available_at = models.DateTimeField(_("earnings available at"), null=True, blank=True, db_index=True)
    payout = models.ForeignKey(Payout, on_delete=models.SET_NULL, null=True, blank=True, related_name="vendor_orders")

    class Meta:
        verbose_name = _("vendor order")
        verbose_name_plural = _("vendor orders")
        ordering = ["-created_at"]
        unique_together = [("order", "vendor")]
        indexes = [models.Index(fields=["vendor", "status"], name="mkt_vorder_vendor_idx")]

    def __str__(self):
        return f"{self.order.order_number} / {self.vendor}"

    @property
    def net_amount(self) -> Decimal:
        """What the vendor earns: sales minus refunds minus commission on what was kept."""
        kept = max(ZERO, self.gross_amount - self.refunded_amount)
        return max(ZERO, (kept - (kept * self.commission_rate)).quantize(Decimal("0.01")))
