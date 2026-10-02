from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_core.utils.pricing import build_line
from flexcommerce_marketplace import services
from flexcommerce_marketplace.models import Vendor
from flexcommerce_orders.services import place_order
from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]

ADDRESS = {"first_name": "Kemi", "last_name": "Ade", "line1": "3 Allen", "city": "Ikeja", "state": "Lagos"}
User = get_user_model()


@pytest.fixture(autouse=True)
def _settings(fc):
    fc(
        PRODUCT_MODELS=["tests.Product"],
        VAT_INCLUSIVE=True,
        MARKETPLACE_COMMISSION_RATE=0.10,
        RETURN_WINDOW_DAYS=7,
        THROTTLING_ENABLED=False,
        VENDOR_MINIMUM_PAYOUT=1000,
    )


@pytest.fixture
def seller(db):
    return User.objects.create_user("seller", "seller@example.com", "x")


@pytest.fixture
def seller2(db):
    return User.objects.create_user("seller2", "seller2@example.com", "x")


@pytest.fixture
def buyer(db):
    return User.objects.create_user("buyer", "buyer@example.com", "x")


@pytest.fixture
def staff(db):
    return User.objects.create_user("staff", "staff@example.com", "x", is_staff=True)


@pytest.fixture
def vendor(seller):
    v = services.apply(seller, name="Mama Put Kitchenware")
    services.set_status(v, Vendor.STATUS_APPROVED)
    return v


@pytest.fixture
def vendor2(seller2):
    v = services.apply(seller2, name="Aba Shoes", commission_rate=None)
    v.commission_rate = Decimal("0.05")
    v.save()
    services.set_status(v, Vendor.STATUS_APPROVED)
    return v


def client_for(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user)
    return c


def order_with(buyer, *specs):
    """specs: (vendor_or_None, price, qty)"""
    lines = []
    for vendor, price, qty in specs:
        product = Product.objects.create(price=Decimal(price), vendor_id=vendor.pk if vendor else None)
        lines.append(build_line(product, qty, key=len(lines)))
    subtotal = sum(line.line_gross for line in lines)
    return place_order(
        lines=lines,
        subtotal=subtotal,
        discount=Decimal("0"),
        tax_total=Decimal("0"),
        shipping_cost=Decimal("0"),
        shipping_vat=Decimal("0"),
        grand_total=subtotal,
        currency="NGN",
        shipping_address=ADDRESS,
        payment_method="card",
        user=buyer,
    )
