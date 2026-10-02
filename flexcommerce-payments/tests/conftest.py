from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_core.utils.pricing import build_line
from flexcommerce_orders.services import place_order
from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]

ADDRESS = {
    "first_name": "Tunde",
    "last_name": "Bello",
    "line1": "1 Marina",
    "city": "Lagos",
    "state": "Lagos",
    "phone": "08011111111",
}


@pytest.fixture(autouse=True)
def _settings(fc):
    fc(
        PRODUCT_MODELS=["tests.Product"],
        VAT_INCLUSIVE=True,
        PAYSTACK_SECRET_KEY="sk_test_abc",
        PAYSTACK_PUBLIC_KEY="pk_test_abc",
        FLUTTERWAVE_SECRET_KEY="FLWSECK_TEST-abc",
        FLUTTERWAVE_WEBHOOK_HASH="flw-hash",
        BANK_TRANSFER_ACCOUNTS=[{"bank_name": "GTBank", "account_name": "Shop Ltd", "account_number": "0123456789"}],
        PAY_ON_DELIVERY_LIMIT=100000,
        PAYMENT_CALLBACK_URL="https://shop.example.com/checkout/done",
        PAYMENT_ALLOWED_CALLBACK_HOSTS=["shop.example.com"],
    )


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tunde", "tunde@example.com", "x")


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("staff", "staff@example.com", "x", is_staff=True)


def api(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user)
    return c


def make_order(user=None, price="25000.00", email=""):
    product = Product.objects.create(price=Decimal(price))
    line = build_line(product, 1)
    return place_order(
        lines=[line],
        subtotal=line.line_gross,
        discount=Decimal("0"),
        tax_total=line.line_vat,
        shipping_cost=Decimal("0"),
        shipping_vat=Decimal("0"),
        grand_total=line.line_gross,
        currency="NGN",
        shipping_address=ADDRESS,
        payment_method="card",
        user=user,
        email=email,
    )


@pytest.fixture
def order(user):
    return make_order(user=user)


@pytest.fixture
def gateway_http():
    """Patch the single HTTP seam used by all gateways. Set ``.responses`` to a list."""
    with (
        mock.patch("flexcommerce_payments.gateways.paystack.request_json") as paystack,
        mock.patch("flexcommerce_payments.gateways.flutterwave.request_json") as flutterwave,
    ):
        yield {"paystack": paystack, "flutterwave": flutterwave}
