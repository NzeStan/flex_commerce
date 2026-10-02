from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_core.utils.pricing import build_line
from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]


@pytest.fixture(autouse=True)
def _vat_exclusive(fc):
    fc(PRODUCT_MODELS=["tests.Product"], VAT_RATE=0.075, VAT_INCLUSIVE=True)


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("buyer", "buyer@example.com", "x")


@pytest.fixture
def staff_client(db):
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("staff", "s@example.com", "x", is_staff=True))
    return c


def make_lines(*specs):
    """specs: (price, qty[, product kwargs]) -> PricedLines (VAT-inclusive prices)."""
    lines = []
    for index, spec in enumerate(specs):
        price, qty = spec[0], spec[1]
        kwargs = spec[2] if len(spec) > 2 else {}
        product = Product.objects.create(price=Decimal(price), **kwargs)
        lines.append(build_line(product, qty, key=index))
    return lines
