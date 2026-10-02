"""
FlexCommerce Pricing: tax categories and price snapshots.

The price/tax pipeline itself lives in ``flexcommerce_core.utils.pricing`` so the
cart works without this app; installing it adds admin-managed tax categories
(standard / zero-rated / exempt / custom rate) that products reference through
a ``tax_category`` attribute.
"""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.conf import fc_setting
from flexcommerce_core.models import TimeStampedUUIDModel
from flexcommerce_core.utils.pricing import DefaultPriceHandler as _CorePriceHandler
from flexcommerce_core.utils.pricing import DefaultTaxHandler as _CoreTaxHandler
from flexcommerce_core.utils.pricing import get_tax_rate, get_unit_price
from flexcommerce_core.utils.vat import compute_tax, round_price, to_decimal


class TaxCategory(TimeStampedUUIDModel):
    """Tax category assigned to products (standard, zero-rated, exempt)."""

    TYPE_STANDARD = "standard"
    TYPE_ZERO_RATED = "zero_rated"
    TYPE_EXEMPT = "exempt"
    TYPE_CHOICES = [
        (TYPE_STANDARD, _("Standard")),
        (TYPE_ZERO_RATED, _("Zero Rated")),
        (TYPE_EXEMPT, _("VAT Exempt")),
    ]

    name = models.CharField(_("name"), max_length=100)
    code = models.SlugField(_("code"), unique=True)
    category_type = models.CharField(_("type"), max_length=20, choices=TYPE_CHOICES, default=TYPE_STANDARD)
    rate = models.DecimalField(
        _("rate"),
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=_("Override VAT rate (e.g. 0.0750). Leave blank to use the global default."),
    )
    description = models.TextField(_("description"), blank=True)

    class Meta:
        verbose_name = _("tax category")
        verbose_name_plural = _("tax categories")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.category_type})"

    def clean(self):
        if self.rate is not None and not (0 <= self.rate < 1):
            raise ValidationError({"rate": _("Rate must be a fraction between 0 and 1.")})

    def get_rate(self) -> Decimal:
        if self.category_type in (self.TYPE_ZERO_RATED, self.TYPE_EXEMPT):
            return Decimal("0.00")
        if self.rate is not None:
            return self.rate
        return to_decimal(fc_setting("VAT_RATE", 0.075))


class PriceBreakdown(TimeStampedUUIDModel):
    """Snapshot of a price computation for a line item."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.UUIDField(_("object ID"), null=True, blank=True)

    quantity = models.PositiveIntegerField(_("quantity"), default=1)
    unit_price_net = models.DecimalField(_("unit price (net)"), max_digits=14, decimal_places=2)
    unit_vat = models.DecimalField(_("unit VAT"), max_digits=14, decimal_places=2, default=Decimal("0.00"))
    unit_price_gross = models.DecimalField(_("unit price (gross)"), max_digits=14, decimal_places=2)
    vat_rate = models.DecimalField(_("VAT rate"), max_digits=5, decimal_places=4, default=Decimal("0.0750"))
    currency = models.CharField(_("currency"), max_length=10, default="NGN")
    line_net = models.DecimalField(_("line net"), max_digits=14, decimal_places=2)
    line_vat = models.DecimalField(_("line VAT"), max_digits=14, decimal_places=2, default=Decimal("0.00"))
    line_gross = models.DecimalField(_("line gross"), max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = _("price breakdown")
        verbose_name_plural = _("price breakdowns")
        indexes = [models.Index(fields=["content_type", "object_id"], name="pricing_breakdown_obj_idx")]

    def __str__(self):
        return f"PriceBreakdown({self.line_gross} {self.currency})"

    @classmethod
    def from_product(cls, product, quantity: int = 1, tax_category: "TaxCategory" = None, **kwargs):
        """Build (unsaved) from the live price pipeline (handlers + hooks)."""
        price = get_unit_price(product, quantity=quantity)
        rate = tax_category.get_rate() if tax_category else get_tax_rate(product)
        if getattr(product, "vat_exempt", False) or getattr(product, "vat_zero_rated", False):
            rate = Decimal("0.00")
        tax_info = compute_tax(price, rate=rate)
        return cls(
            quantity=quantity,
            unit_price_net=tax_info["net"],
            unit_vat=tax_info["vat"],
            unit_price_gross=tax_info["gross"],
            vat_rate=rate,
            currency=fc_setting("CURRENCY", "NGN"),
            line_net=round_price(tax_info["net"] * quantity),
            line_vat=round_price(tax_info["vat"] * quantity),
            line_gross=round_price(tax_info["gross"] * quantity),
            **kwargs,
        )


# ── Strategy handlers (importable handlers) ─────────────────────────────────


class BasePriceHandler:
    """
    Subclass to implement custom pricing (tiered, B2B, per-customer...).
    Register via ``FLEXCOMMERCE = {"PRICE_HANDLER": "myapp.pricing.MyHandler"}``.
    """

    def get_price(self, product, user=None, quantity=1, cart=None) -> Decimal:
        raise NotImplementedError

    def get_price_display(self, product, user=None) -> dict:
        return compute_tax(self.get_price(product, user=user))


class DefaultPriceHandler(_CorePriceHandler, BasePriceHandler):
    """Reads ``product.price``."""


class BaseTaxHandler:
    """Subclass to implement custom tax rules; register as ``TAX_HANDLER``."""

    def get_rate(self, product=None, user=None) -> Decimal:
        raise NotImplementedError

    def compute(self, price, product=None, user=None) -> dict:
        return compute_tax(price, rate=self.get_rate(product, user))


class DefaultTaxHandler(_CoreTaxHandler, BaseTaxHandler):
    """Nigerian VAT with product-level exemptions and tax categories."""
