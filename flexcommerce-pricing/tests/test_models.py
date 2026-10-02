"""Tests for flexcommerce_pricing models and handlers."""

from decimal import Decimal

import pytest

from flexcommerce_core.exceptions import PricingError
from flexcommerce_pricing.models import (
    DefaultPriceHandler,
    DefaultTaxHandler,
    PriceBreakdown,
    TaxCategory,
)

# ── TaxCategory ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTaxCategory:
    def test_standard_type_uses_global_vat_rate(self, standard_tax_category, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075}
        rate = standard_tax_category.get_rate()
        assert rate == Decimal("0.075")

    def test_zero_rated_type_returns_zero(self, zero_rate_category):
        rate = zero_rate_category.get_rate()
        assert rate == Decimal("0.00")

    def test_exempt_type_returns_zero(self, exempt_category):
        rate = exempt_category.get_rate()
        assert rate == Decimal("0.00")

    def test_custom_rate_overrides_global(self, custom_rate_category):
        rate = custom_rate_category.get_rate()
        assert rate == Decimal("0.05")

    def test_str_contains_name_and_type(self, standard_tax_category):
        s = str(standard_tax_category)
        assert "Standard" in s
        assert "standard" in s

    def test_unique_code(self, db):
        from django.db import IntegrityError

        TaxCategory.objects.create(name="A", code="unique-code", category_type=TaxCategory.TYPE_STANDARD)
        with pytest.raises(IntegrityError):
            TaxCategory.objects.create(name="B", code="unique-code", category_type=TaxCategory.TYPE_EXEMPT)


# ── PriceBreakdown ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPriceBreakdown:
    def test_from_product_standard_rate(self, standard_product, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075, "VAT_INCLUSIVE": False}
        bd = PriceBreakdown.from_product(standard_product, quantity=1)
        assert bd.unit_price_net == Decimal("1000.00")
        assert bd.unit_vat == Decimal("75.00")
        assert bd.unit_price_gross == Decimal("1075.00")
        assert bd.line_gross == Decimal("1075.00")

    def test_from_product_quantity_multiplies_line(self, standard_product, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075, "VAT_INCLUSIVE": False}
        bd = PriceBreakdown.from_product(standard_product, quantity=3)
        assert bd.line_net == Decimal("3000.00")
        assert bd.line_vat == Decimal("225.00")
        assert bd.line_gross == Decimal("3225.00")

    def test_from_product_vat_exempt_has_zero_vat(self, exempt_product, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075, "VAT_INCLUSIVE": False}
        bd = PriceBreakdown.from_product(exempt_product, quantity=1)
        assert bd.unit_vat == Decimal("0.00")
        assert bd.unit_price_gross == bd.unit_price_net

    def test_from_product_zero_rated_has_zero_vat(self, zero_rated_product, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075, "VAT_INCLUSIVE": False}
        bd = PriceBreakdown.from_product(zero_rated_product, quantity=1)
        assert bd.unit_vat == Decimal("0.00")

    def test_from_product_with_custom_tax_category(self, standard_product, custom_rate_category, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075, "VAT_INCLUSIVE": False}
        bd = PriceBreakdown.from_product(standard_product, quantity=1, tax_category=custom_rate_category)
        assert bd.vat_rate == Decimal("0.05")
        assert bd.unit_vat == Decimal("50.00")

    def test_from_product_str(self, standard_product, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075, "VAT_INCLUSIVE": False}
        bd = PriceBreakdown.from_product(standard_product, quantity=1)
        assert "PriceBreakdown" in str(bd)
        assert "NGN" in str(bd)


# ── DefaultPriceHandler ───────────────────────────────────────────────────────


class TestDefaultPriceHandler:
    def test_get_price_returns_product_price(self, standard_product):
        handler = DefaultPriceHandler()
        price = handler.get_price(standard_product)
        assert price == Decimal("1000.00")

    def test_get_price_raises_if_no_price_attr(self):
        from unittest.mock import MagicMock

        product = MagicMock(spec=[])  # no attributes
        handler = DefaultPriceHandler()
        with pytest.raises(PricingError):
            handler.get_price(product)

    def test_get_price_display_returns_dict_with_net_vat_gross(self, standard_product, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075, "VAT_INCLUSIVE": False}
        handler = DefaultPriceHandler()
        display = handler.get_price_display(standard_product)
        assert "net" in display
        assert "vat" in display
        assert "gross" in display


# ── DefaultTaxHandler ─────────────────────────────────────────────────────────


class TestDefaultTaxHandler:
    def test_compute_standard_product(self, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075, "VAT_INCLUSIVE": False}
        handler = DefaultTaxHandler()
        result = handler.compute(Decimal("1000.00"))
        assert result["vat"] == Decimal("75.00")
        assert result["gross"] == Decimal("1075.00")

    def test_compute_vat_exempt_product(self, exempt_product, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075}
        handler = DefaultTaxHandler()
        result = handler.compute(Decimal("500.00"), product=exempt_product)
        assert result["vat"] == Decimal("0.00")
        assert result["gross"] == Decimal("500.00")

    def test_compute_zero_rated_product(self, zero_rated_product, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075}
        handler = DefaultTaxHandler()
        result = handler.compute(Decimal("800.00"), product=zero_rated_product)
        assert result["vat"] == Decimal("0.00")

    @pytest.mark.django_db
    def test_compute_with_tax_category_override(self, standard_product, custom_rate_category, settings):
        settings.FLEXCOMMERCE = {"VAT_RATE": 0.075}
        standard_product.tax_category = custom_rate_category
        handler = DefaultTaxHandler()
        result = handler.compute(Decimal("1000.00"), product=standard_product)
        assert result["vat"] == Decimal("50.00")
