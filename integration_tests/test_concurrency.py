"""
Race conditions under real concurrency (PostgreSQL only — SQLite has a single writer).

Each test starts N threads behind a barrier so they hit the database at the same
moment, then asserts the invariants a big shop depends on.
"""

import threading
from decimal import Decimal

import pytest
from django.db import close_old_connections, connection

from flexcommerce_core.exceptions import FlexCommerceError

from .conftest import ADDRESS

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _postgres_only():
    if connection.vendor == "sqlite":
        pytest.skip("concurrency tests need PostgreSQL: set FC_TEST_DATABASE_URL")


def race(n, fn):
    barrier = threading.Barrier(n)
    ok, errors = [], []

    def worker(i):
        try:
            barrier.wait()
            ok.append(fn(i))
        except FlexCommerceError as exc:
            errors.append(exc)
        except Exception as exc:  # anything else is a bug
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return ok, errors


def make_buyers(user_model, n):
    return [user_model.objects.create_user(f"buyer{i}", f"buyer{i}@example.com", "x") for i in range(n)]


def filled_cart(user, product, qty=1):
    from flexcommerce_cart.models import Cart
    from flexcommerce_cart.services import CartService

    cart = Cart.objects.create(user=user)
    CartService(cart).add_item(product, qty)
    return cart


def checkout(cart_pk):
    from flexcommerce_cart.models import Cart
    from flexcommerce_checkout.services import CheckoutService

    cart = Cart.objects.get(pk=cart_pk)
    return CheckoutService(cart).execute("pay_on_delivery", ADDRESS, shipping_method_id=None).order


def test_last_units_are_never_oversold(user_model, catalog, settings):
    from flexcommerce_inventory.models import InventoryItem
    from flexcommerce_orders.models import Order

    settings.FLEXCOMMERCE = {**settings.FLEXCOMMERCE, "CHECKOUT_REQUIRE_SHIPPING": False}
    black = catalog["black"]
    InventoryItem.objects.get(object_id=black.pk).adjust(3)
    carts = [filled_cart(u, black).pk for u in make_buyers(user_model, 12)]
    InventoryItem.objects.get(object_id=black.pk).adjust(3)  # carts were filled while stock existed
    ok, errors = race(12, lambda i: checkout(carts[i]))
    stock = InventoryItem.objects.get(object_id=black.pk)
    assert len(ok) == 3 and Order.objects.count() == 3
    assert all(e.code == "insufficient_stock" for e in errors), errors
    assert (stock.on_hand, stock.reserved, stock.sold) == (0, 0, 3)


def test_coupon_usage_limit_holds_under_load(user_model, catalog, settings):
    from flexcommerce_discounts.models import Coupon, CouponUsage
    from flexcommerce_orders.models import Order

    settings.FLEXCOMMERCE = {**settings.FLEXCOMMERCE, "CHECKOUT_REQUIRE_SHIPPING": False, "PAY_ON_DELIVERY_LIMIT": None}
    Coupon.objects.create(code="FIRST2", name="x", coupon_type=Coupon.TYPE_FIXED, value=Decimal("1000"), usage_limit=2)
    white = catalog["white"]
    carts = []
    for user in make_buyers(user_model, 8):
        cart = filled_cart(user, white)
        cart.coupon_code = "FIRST2"
        cart.save(update_fields=["coupon_code"])
        carts.append(cart.pk)
    ok, errors = race(8, lambda i: checkout(carts[i]))
    discounted = Order.objects.filter(coupon_code="FIRST2").count()
    assert Coupon.objects.get().used_count == 2 and CouponUsage.objects.count() == 2
    assert discounted == 2
    # Buyers who lost the race either failed cleanly or checked out without the discount.
    assert all(isinstance(e, FlexCommerceError) for e in errors), errors
    assert len(ok) + len(errors) == 8


def test_flash_sale_quantity_cap_holds(user_model, catalog, settings):
    from datetime import timedelta

    from django.contrib.contenttypes.models import ContentType
    from django.utils import timezone

    from flexcommerce_discounts.models import FlashSale, FlashSaleItem
    from flexcommerce_orders.models import Order

    settings.FLEXCOMMERCE = {**settings.FLEXCOMMERCE, "CHECKOUT_REQUIRE_SHIPPING": False, "PAY_ON_DELIVERY_LIMIT": None}
    black = catalog["black"]
    now = timezone.now()
    sale = FlashSale.objects.create(
        name="Flash", starts_at=now - timedelta(minutes=5), ends_at=now + timedelta(hours=1)
    )
    FlashSaleItem.objects.create(
        sale=sale,
        content_type=ContentType.objects.get_for_model(black),
        object_id=black.pk,
        sale_price=Decimal("150000"),
        quantity_limit=2,
    )
    carts = [filled_cart(u, black).pk for u in make_buyers(user_model, 6)]
    ok, errors = race(6, lambda i: checkout(carts[i]))
    item = FlashSaleItem.objects.get()
    at_sale_price = Order.objects.filter(grand_total=Decimal("150000.00")).count()
    assert item.sold_quantity == 2 and at_sale_price == 2
    assert all(isinstance(e, FlexCommerceError) for e in errors), errors


def test_same_customer_double_submit_creates_one_order(customer, catalog, settings):
    from flexcommerce_checkout.services import CheckoutService
    from flexcommerce_orders.models import Order

    settings.FLEXCOMMERCE = {**settings.FLEXCOMMERCE, "CHECKOUT_REQUIRE_SHIPPING": False}
    cart = filled_cart(customer, catalog["black"])

    def submit(i):
        from flexcommerce_cart.models import Cart

        return (
            CheckoutService(Cart.objects.get(pk=cart.pk))
            .execute("pay_on_delivery", ADDRESS, idempotency_key="tap-tap-tap")
            .order.pk
        )

    ok, errors = race(6, submit)
    assert Order.objects.count() == 1 and set(ok) == {Order.objects.get().pk}
    assert all(isinstance(e, FlexCommerceError) for e in errors), errors


def test_wallet_cannot_be_overdrawn(customer):
    from flexcommerce_payments.models import Wallet
    from flexcommerce_payments.services import WalletService

    WalletService.credit(customer, Decimal("5000"), "seed")
    ok, errors = race(10, lambda i: WalletService.debit(customer, Decimal("1000"), f"spend-{i}"))
    assert len(ok) == 5 and Wallet.objects.get(user=customer).balance == Decimal("0.00")
