"""
Price & tax pipeline shared by cart, checkout and pricing.

    unit price = PRICE_HANDLER.get_price(product)          (default: product.price)
               → "price.modify" hooks (e.g. flash sales)   (flexcommerce_discounts)
    VAT rate   = TAX_HANDLER.get_rate(product)             (default: VAT_RATE / exempt flags;
                                                            flexcommerce_pricing adds tax categories)

``VAT_INCLUSIVE`` decides whether the unit price already contains VAT.
Amounts are rounded per unit and multiplied by quantity, which keeps line
totals and invoices consistent.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from .. import hooks
from ..exceptions import PricingError
from .helpers import registry
from .vat import ZERO, compute_tax, get_vat_rate, is_vat_inclusive, round_price, to_decimal


class DefaultPriceHandler:
    """Reads ``product.price``."""

    def get_price(self, product, user=None, quantity=1, cart=None) -> Decimal:
        price = getattr(product, "price", None)
        if price is None:
            raise PricingError(f"Product {getattr(product, 'pk', product)} has no 'price' attribute.")
        return to_decimal(price)


class DefaultTaxHandler:
    """Nigerian VAT: global rate, exempt / zero-rated flags, optional ``tax_category``."""

    def get_rate(self, product=None, user=None) -> Decimal:
        if product is not None:
            if getattr(product, "vat_exempt", False) or getattr(product, "vat_zero_rated", False):
                return ZERO
            category = getattr(product, "tax_category", None)
            if category is not None and hasattr(category, "get_rate"):
                return to_decimal(category.get_rate())
        return get_vat_rate()

    # Simple API
    def compute(self, price, product=None, user=None) -> dict:
        return compute_tax(price, rate=self.get_rate(product, user))


registry.set_default("PRICE_HANDLER", DefaultPriceHandler)
registry.set_default("TAX_HANDLER", DefaultTaxHandler)


def get_unit_price(product, user=None, quantity=1, cart=None) -> Decimal:
    price = registry.get("PRICE_HANDLER").get_price(product, user=user, quantity=quantity, cart=cart)
    price = hooks.run_pipeline("price.modify", to_decimal(price), product, user=user, quantity=quantity)
    return max(ZERO, round_price(price))


def get_tax_rate(product=None, user=None) -> Decimal:
    return to_decimal(registry.get("TAX_HANDLER").get_rate(product, user=user))


def line_amounts(unit_price, quantity: int, rate, inclusive: bool = None) -> dict:
    """Return unit and line net / vat / gross for ``quantity`` units."""
    if inclusive is None:
        inclusive = is_vat_inclusive()
    unit = compute_tax(unit_price, rate=rate, inclusive=inclusive)
    qty = int(quantity)
    return {
        "unit_price": round_price(unit_price),
        "unit_net": unit["net"],
        "unit_vat": unit["vat"],
        "unit_gross": unit["gross"],
        "rate": unit["rate"],
        "line_net": round_price(unit["net"] * qty),
        "line_vat": round_price(unit["vat"] * qty),
        "line_gross": round_price(unit["gross"] * qty),
    }


def price_product(product, quantity=1, user=None, cart=None) -> dict:
    """Full server-side pricing of ``quantity`` units of ``product``."""
    unit_price = get_unit_price(product, user=user, quantity=quantity, cart=cart)
    return line_amounts(unit_price, quantity, get_tax_rate(product, user=user))


@dataclass
class PricedLine:
    """
    A priced line shared by cart, checkout, discounts and shipping.
    ``parent_id`` is the id of the parent product for variant-style models
    (``product.product_id``) so coupons can target whole products.
    """

    product: object
    quantity: int
    unit_price: Decimal
    unit_net: Decimal
    unit_vat: Decimal
    unit_gross: Decimal
    rate: Decimal
    line_net: Decimal
    line_vat: Decimal
    line_gross: Decimal
    key: object = None  # caller's identifier, e.g. a CartItem pk
    product_id: str = ""
    parent_id: str = ""
    content_type_id: int = None
    vendor_id: str = ""
    category_ids: set = field(default_factory=set)
    weight: Decimal = ZERO
    discount: Decimal = ZERO  # allocated share of the order discount

    @property
    def ids(self):
        return {i for i in (self.product_id, self.parent_id) if i}


def build_line(product, quantity, key=None, user=None, cart=None) -> PricedLine:
    from django.contrib.contenttypes.models import ContentType

    from .products import product_category_ids, product_vendor_id, product_weight

    amounts = price_product(product, quantity=quantity, user=user, cart=cart)
    parent = getattr(product, "product_id", None)
    vendor = product_vendor_id(product)
    return PricedLine(
        product=product,
        quantity=int(quantity),
        key=key,
        product_id=str(product.pk),
        parent_id=str(parent) if parent else "",
        content_type_id=ContentType.objects.get_for_model(product).pk,
        vendor_id=str(vendor) if vendor else "",
        category_ids=product_category_ids(product),
        weight=product_weight(product),
        **amounts,
    )


def allocate_discount(gross_total, vat_total, discount) -> dict:
    """
    Apply a gross ``discount`` to merchandise totals and reduce VAT proportionally
    (the discount lowers the taxable base). Returns ``{total, vat, discount}``.
    """
    gross_total = to_decimal(gross_total)
    discount = min(max(to_decimal(discount), ZERO), gross_total)
    if gross_total <= 0:
        return {"total": ZERO, "vat": ZERO, "discount": ZERO}
    ratio = (gross_total - discount) / gross_total
    return {
        "total": round_price(gross_total - discount),
        "vat": round_price(to_decimal(vat_total) * ratio),
        "discount": round_price(discount),
    }
