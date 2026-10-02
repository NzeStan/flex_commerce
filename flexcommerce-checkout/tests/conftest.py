from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_cart.models import Cart
from flexcommerce_cart.services import CartService
from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]

ADDRESS = {
    "first_name": "Ngozi",
    "last_name": "Adeleke",
    "line1": "5 Broad St",
    "city": "Lagos",
    "state": "Lagos",
    "country": "Nigeria",
    "phone": "08099887766",
    "landmark": "Opposite CMS bus stop",
}


@pytest.fixture(autouse=True)
def _settings(fc):
    fc(
        PRODUCT_MODELS=["tests.Product"],
        VAT_RATE=0.075,
        VAT_INCLUSIVE=True,
        PAY_ON_DELIVERY_LIMIT=500000,
        THROTTLING_ENABLED=False,
    )


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("buyer", "buyer@test.com", "pass")


@pytest.fixture
def product(db):
    return Product.objects.create(name="Headphones", price=Decimal("15000.00"))


@pytest.fixture
def cart(user, product):
    cart = Cart.objects.create(user=user)
    CartService(cart).add_item(product, 2)
    return cart


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client
