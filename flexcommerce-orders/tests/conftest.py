from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_core.utils.pricing import build_line
from flexcommerce_orders.models import Order
from flexcommerce_orders.services import place_order
from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]

ADDRESS = {
    "first_name": "Chioma",
    "last_name": "Eze",
    "line1": "4 Ogui Rd",
    "city": "Enugu",
    "state": "Enugu",
    "phone": "08030000000",
}


@pytest.fixture(autouse=True)
def _settings(fc):
    fc(PRODUCT_MODELS=["tests.Product"], VAT_RATE=0.075, VAT_INCLUSIVE=True)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("ada", "ada@example.com", "x", first_name="Ada", last_name="Obi")


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user("bola", "bola@example.com", "x")


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("staff", "staff@example.com", "x", is_staff=True)


def client_for(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user)
    return c


def make_order(
    user=None,
    qtys=(2, 1),
    prices=("10000", "5000"),
    discount=Decimal("0"),
    shipping=Decimal("1500"),
    payment_method=Order.PAYMENT_METHOD_CARD,
    email="",
    **kwargs,
):
    lines = []
    for index, (qty, price) in enumerate(zip(qtys, prices, strict=True)):
        product = Product.objects.create(name=f"P{index}", sku=f"SKU{index}", price=Decimal(price))
        lines.append(build_line(product, qty, key=index))
    subtotal = sum(line.line_gross for line in lines)
    if discount:
        lines[0].discount = discount
    return place_order(
        lines=lines,
        subtotal=subtotal,
        discount=discount,
        tax_total=sum(line.line_vat for line in lines),
        shipping_cost=shipping,
        shipping_vat=Decimal("0"),
        grand_total=subtotal - discount + shipping,
        currency="NGN",
        shipping_address=ADDRESS,
        payment_method=payment_method,
        user=user,
        email=email,
        **kwargs,
    )


@pytest.fixture
def order(user):
    return make_order(user=user)
