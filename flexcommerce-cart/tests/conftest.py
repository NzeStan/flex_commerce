from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]


@pytest.fixture(autouse=True)
def _settings(fc):
    fc(PRODUCT_MODELS=["tests.Product"], VAT_RATE=0.075, VAT_INCLUSIVE=False)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("buyer", "buyer@example.com", "pass1234")


@pytest.fixture
def product(db):
    return Product.objects.create(name="Indomie Carton", price=Decimal("10000.00"), sku="INDO-40")


@pytest.fixture
def product2(db):
    return Product.objects.create(name="Peak Milk", price=Decimal("2500.00"), sku="PEAK-1")


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def auth_api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c
