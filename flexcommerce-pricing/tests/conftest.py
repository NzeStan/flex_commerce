from decimal import Decimal

import pytest

from flexcommerce_pricing.models import TaxCategory
from tests.models import Product


@pytest.fixture
def standard_product(db):
    return Product.objects.create(name="Standard", price=Decimal("1000.00"))


@pytest.fixture
def exempt_product(db):
    return Product.objects.create(name="Exempt", price=Decimal("500.00"), vat_exempt=True)


@pytest.fixture
def zero_rated_product(db):
    return Product.objects.create(name="Zero Rated", price=Decimal("800.00"), vat_zero_rated=True)


@pytest.fixture
def standard_tax_category(db):
    return TaxCategory.objects.create(name="Standard", code="standard", category_type=TaxCategory.TYPE_STANDARD)


@pytest.fixture
def zero_rate_category(db):
    return TaxCategory.objects.create(name="Zero", code="zero", category_type=TaxCategory.TYPE_ZERO_RATED)


@pytest.fixture
def exempt_category(db):
    return TaxCategory.objects.create(name="Exempt", code="exempt", category_type=TaxCategory.TYPE_EXEMPT)


@pytest.fixture
def custom_rate_category(db):
    return TaxCategory.objects.create(
        name="Custom 5%",
        code="custom5",
        category_type=TaxCategory.TYPE_STANDARD,
        rate=Decimal("0.05"),
    )
