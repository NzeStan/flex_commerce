from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_core.utils.pricing import build_line
from flexcommerce_orders.services import OrderService, place_order
from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]


@pytest.fixture(autouse=True)
def _settings(fc):
    fc(PRODUCT_MODELS=["tests.Product"], VAT_RATE=0.075, VAT_INCLUSIVE=True, MARKETPLACE_MODE=True)


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("staff", "s@example.com", "x", is_staff=True)


@pytest.fixture
def staff_client(staff):
    c = APIClient()
    c.force_authenticate(staff)
    return c


def sale(
    email="a@example.com",
    price="10750",
    qty=1,
    state="Lagos",
    method="card",
    coupon="",
    confirm=True,
    vendor_id=None,
    product=None,
    discount=Decimal("0"),
):
    product = product or Product.objects.create(
        name=f"P-{price}", sku=f"S{price}", price=Decimal(price), vendor_id=vendor_id
    )
    line = build_line(product, qty)
    line.discount = discount
    order = place_order(
        lines=[line],
        subtotal=line.line_gross,
        discount=discount,
        tax_total=line.line_vat,
        shipping_cost=Decimal("0"),
        shipping_vat=Decimal("0"),
        grand_total=line.line_gross - discount,
        currency="NGN",
        shipping_address={"state": state, "first_name": "A", "last_name": "B"},
        payment_method=method,
        email=email,
        coupon_code=coupon,
    )
    if confirm:
        OrderService(order).mark_paid(reference=f"R{order.pk.hex[:6]}")
        order.refresh_from_db()
    return order
