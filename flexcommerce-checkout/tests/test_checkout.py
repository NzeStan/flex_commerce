"""Checkout with only core + cart + orders installed (every other app optional)."""

from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from flexcommerce_cart.models import Cart
from flexcommerce_checkout.services import (
    CheckoutResult,
    CheckoutService,
    PayOnDeliveryHandler,
    PriceChangedError,
    available_payment_methods,
)
from flexcommerce_core.exceptions import (
    CartLockedError,
    CheckoutError,
    DuplicateCheckoutError,
    EmptyCartCheckoutError,
    GuestCheckoutDisabledError,
    PaymentError,
)
from flexcommerce_core.models import Address
from flexcommerce_core.utils.helpers import registry
from flexcommerce_orders.models import Order
from tests.models import Product

from .conftest import ADDRESS

pytestmark = pytest.mark.django_db
D = Decimal


def checkout(client, **payload):
    body = {"payment_method": "pay_on_delivery", "shipping_address": ADDRESS, **payload}
    return client.post("/api/checkout/", body, format="json")


class TestService:
    def test_pay_on_delivery_confirms_immediately(self, cart, user):
        result = CheckoutService(cart).execute("pay_on_delivery", ADDRESS)
        order = result.order
        assert order.status == Order.STATUS_CONFIRMED and order.payment_status == Order.PAYMENT_PENDING
        assert order.grand_total == D("30000.00") and order.tax_total == D("2093.02")
        assert order.email == "buyer@test.com" and order.phone == "08099887766"
        assert order.shipping_address["landmark"] == "Opposite CMS bus stop"
        assert order.items.get().quantity == 2 and order.payment_due_at is None
        assert result.payment_result["reference"] == f"POD-{order.order_number}"
        cart.refresh_from_db()
        assert cart.status == Cart.STATUS_ORDERED

    def test_totals_use_fresh_prices(self, cart, product):
        Product.objects.filter(pk=product.pk).update(price=D("20000"))
        order = CheckoutService(cart).execute("pay_on_delivery", ADDRESS).order
        assert order.grand_total == D("40000.00")

    def test_expected_total_guard(self, cart, product):
        with pytest.raises(PriceChangedError) as exc:
            CheckoutService(cart).execute("pay_on_delivery", ADDRESS, expected_total=D("29999.99"))
        assert exc.value.extra["actual"] == "30000.00"
        assert CheckoutService(cart).execute("pay_on_delivery", ADDRESS, expected_total=D("30000.00")).order

    def test_card_without_gateway_leaves_pending_with_deadline(self, cart):
        result = CheckoutService(cart).execute("card", ADDRESS)
        assert result.order.status == Order.STATUS_PENDING and result.order.payment_due_at is not None
        assert result.payment_result == {"status": "pending", "reference": ""}

    def test_bank_transfer_deadline(self, cart):
        order = CheckoutService(cart).execute("bank_transfer", ADDRESS).order
        hours = (order.payment_due_at - order.created_at).total_seconds() / 3600
        assert 47.9 < hours <= 48.1

    def test_unknown_method(self, cart):
        with pytest.raises(PaymentError):
            CheckoutService(cart).execute("bitcoin", ADDRESS)

    def test_pod_limit(self, cart, fc):
        fc(PRODUCT_MODELS=["tests.Product"], PAY_ON_DELIVERY_LIMIT=10000)
        with pytest.raises(PaymentError) as exc:
            CheckoutService(cart).execute("pay_on_delivery", ADDRESS)
        assert exc.value.code == "pod_limit_exceeded"
        cart.refresh_from_db()
        assert cart.status == Cart.STATUS_ACTIVE  # rolled back

    def test_address_validation(self, cart, user):
        with pytest.raises(CheckoutError) as exc:
            CheckoutService(cart).execute("pay_on_delivery", {"first_name": "x"})
        assert exc.value.code == "invalid_address" and "line1" in exc.value.extra
        with pytest.raises(CheckoutError):
            CheckoutService(cart).execute("pay_on_delivery", shipping_address_id="00000000-0000-0000-0000-000000000000")
        saved = Address.objects.create(user=user, **ADDRESS)
        order = CheckoutService(cart).execute("pay_on_delivery", shipping_address_id=saved.pk).order
        assert order.shipping_address["line1"] == "5 Broad St"

    def test_billing_address_and_note(self, cart):
        billing = {**ADDRESS, "line1": "Billing Rd"}
        order = (
            CheckoutService(cart)
            .execute("pay_on_delivery", ADDRESS, billing_address=billing, customer_note="Call first")
            .order
        )
        assert order.billing_address["line1"] == "Billing Rd" and order.customer_note == "Call first"

    def test_addresses_saved_once(self, cart, user, product):
        CheckoutService(cart).execute("pay_on_delivery", ADDRESS)
        cart2 = Cart.objects.create(user=user)
        from flexcommerce_cart.services import CartService

        CartService(cart2).add_item(product)
        CheckoutService(cart2).execute("pay_on_delivery", ADDRESS)
        assert Address.objects.filter(user=user).count() == 1
        assert Address.objects.get(user=user).is_default

    def test_empty_locked_and_used_carts(self, user, cart):
        empty = Cart.objects.create(session_key="e")
        with pytest.raises(EmptyCartCheckoutError):
            CheckoutService(empty).execute("pay_on_delivery", ADDRESS, email="g@x.com")
        cart.lock()
        with pytest.raises(CartLockedError):
            CheckoutService(cart).execute("pay_on_delivery", ADDRESS)
        cart.unlock()
        CheckoutService(cart).execute("pay_on_delivery", ADDRESS)
        with pytest.raises(CheckoutError) as exc:
            CheckoutService(cart).execute("pay_on_delivery", ADDRESS)
        assert exc.value.code == "cart_inactive"

    def test_idempotent_replay(self, cart, user, product):
        first = CheckoutService(cart).execute("pay_on_delivery", ADDRESS, idempotency_key="abc-123")
        replay = CheckoutService(cart).execute("pay_on_delivery", ADDRESS, idempotency_key="abc-123")
        assert replay.replayed and replay.order.pk == first.order.pk and Order.objects.count() == 1
        stranger_cart = Cart.objects.create(session_key="s")
        from flexcommerce_cart.services import CartService

        CartService(stranger_cart).add_item(product)
        with pytest.raises(DuplicateCheckoutError):
            CheckoutService(stranger_cart).execute(
                "pay_on_delivery", ADDRESS, idempotency_key="abc-123", email="s@x.com"
            )

    def test_guest_rules(self, product, fc):
        cart = Cart.objects.create(session_key="guest")
        from flexcommerce_cart.services import CartService

        CartService(cart).add_item(product)
        with pytest.raises(CheckoutError) as exc:
            CheckoutService(cart).execute("pay_on_delivery", ADDRESS)
        assert exc.value.code == "email_required"
        order = CheckoutService(cart).execute("pay_on_delivery", ADDRESS, email="Guest@Example.com").order
        assert order.user is None and order.email == "guest@example.com"
        fc(PRODUCT_MODELS=["tests.Product"], GUEST_CHECKOUT=False)
        with pytest.raises(GuestCheckoutDisabledError):
            CheckoutService(cart).execute("pay_on_delivery", ADDRESS, email="g@x.com")

    def test_failure_rolls_everything_back(self, cart, monkeypatch):
        from flexcommerce_orders import services as order_services

        def broken(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(order_services, "place_order", broken)
        with pytest.raises(RuntimeError):
            CheckoutService(cart).execute("pay_on_delivery", ADDRESS)
        cart.refresh_from_db()
        assert cart.status == Cart.STATUS_ACTIVE and Order.objects.count() == 0

    def test_legacy_payment_handler(self, cart):
        class Instant:
            def initiate(self, order, amount, method, **kwargs):
                return {"status": "success", "reference": "LEG-1", "redirect_url": None}

        registry.register("PAYMENT_HANDLER", Instant)
        try:
            result = CheckoutService(cart).execute("card", ADDRESS)
        finally:
            registry.unregister("PAYMENT_HANDLER")
        order = Order.objects.get()
        assert (
            order.is_paid and order.status == Order.STATUS_CONFIRMED and result.payment_result["reference"] == "LEG-1"
        )

    def test_legacy_pod_handler_and_result(self):
        order = Order(order_number="X1", grand_total=D("10"))
        assert PayOnDeliveryHandler().initiate(order, D("10"), "pay_on_delivery")["reference"] == "POD-X1"
        assert PayOnDeliveryHandler().verify("r") == {"status": "pending", "reference": "r"}
        result = CheckoutResult(order=order, payment_result={"redirect_url": "https://pay"})
        assert result.requires_redirect and result.redirect_url == "https://pay"

    def test_wallet_without_payments_app(self, cart):
        with pytest.raises(PaymentError):
            CheckoutService(cart).execute("wallet", ADDRESS)
        cart.refresh_from_db()
        assert cart.status == Cart.STATUS_ACTIVE

    def test_payment_methods_without_payments_app(self):
        assert [m["key"] for m in available_payment_methods()] == [
            "card",
            "bank_transfer",
            "wallet",
            "pay_on_delivery",
        ]

    def test_preview(self, cart):
        totals = CheckoutService(cart).preview(shipping_address=ADDRESS)
        assert totals["grand_total"] == D("30000.00") and totals["shipping_option"] is None


class TestAPI:
    def test_checkout_endpoint(self, cart, auth_client):
        resp = checkout(auth_client)
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["order"]["status"] == "confirmed" and body["requires_redirect"] is False
        assert "access_token" not in body

    def test_guest_checkout_endpoint(self, product):
        guest = APIClient()
        token = guest.post("/api/cart/add/", {"product_id": str(product.pk)}, format="json").json()["cart_token"]
        resp = checkout(guest, email="guest@x.com")
        assert resp.status_code == 201 and resp.json()["access_token"]
        mobile = APIClient(HTTP_X_CART_TOKEN=token)
        assert checkout(mobile, email="guest@x.com").status_code == 400  # cart already ordered

    def test_replay_returns_200(self, cart, auth_client):
        assert checkout(auth_client, idempotency_key="k1").status_code == 201
        again = checkout(auth_client, idempotency_key="k1")
        assert again.status_code == 200 and again.json()["replayed"] is True

    def test_guest_replay_by_cart_token_and_foreign_key(self, product):
        guest = APIClient()
        token = guest.post("/api/cart/add/", {"product_id": str(product.pk)}, format="json").json()["cart_token"]
        assert checkout(guest, email="g@x.com", idempotency_key="g1").status_code == 201
        mobile = APIClient(HTTP_X_CART_TOKEN=token)
        again = checkout(mobile, email="g@x.com", idempotency_key="g1")
        assert again.status_code == 200 and again.json()["access_token"]
        assert checkout(APIClient(), email="x@x.com", idempotency_key="g1").status_code == 409

    def test_validation_errors(self, auth_client, cart):
        assert (
            auth_client.post("/api/checkout/", {"payment_method": "pay_on_delivery"}, format="json").status_code == 400
        )
        assert checkout(auth_client, shipping_address={"city": "x"}).json()["error"] == "invalid_address"
        assert checkout(APIClient(), email="a@b.com").json()["error"] == "empty_cart_checkout"

    def test_preview_shipping_options_and_methods(self, cart, auth_client):
        preview = auth_client.post("/api/checkout/preview/", {"shipping_address": ADDRESS}, format="json").json()
        assert preview["grand_total"] == "30000.00" and preview["shipping"] == "0.00"
        options = auth_client.post("/api/checkout/shipping-options/", {"state": "Lagos"}, format="json").json()
        assert options == {"deliverable": True, "zone": None, "options": []}
        methods = auth_client.get("/api/checkout/payment-methods/").json()
        assert {m["key"] for m in methods} == {"card", "bank_transfer", "wallet", "pay_on_delivery"}

    def test_throttled(self, cart, auth_client, fc):
        fc(PRODUCT_MODELS=["tests.Product"], THROTTLE_RATES={"checkout": "1/minute"})
        checkout(auth_client, shipping_address={"city": "x"})
        assert checkout(auth_client).status_code == 429


@pytest.mark.django_db(transaction=True)
def test_concurrent_double_submit_creates_one_order(user, product):
    import threading

    from django.db import close_old_connections, connection

    if connection.vendor == "sqlite":
        pytest.skip("needs PostgreSQL (FC_TEST_DATABASE_URL)")
    from flexcommerce_cart.services import CartService

    cart = Cart.objects.create(user=user)
    CartService(cart).add_item(product, 1)
    barrier = threading.Barrier(5)
    results, errors = [], []

    def submit():
        try:
            barrier.wait()
            fresh = Cart.objects.get(pk=cart.pk)
            results.append(CheckoutService(fresh).execute("pay_on_delivery", ADDRESS, idempotency_key="double"))
        except Exception as exc:
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=submit) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert Order.objects.count() == 1
    assert {r.order.pk for r in results} == {Order.objects.get().pk}
    assert all(isinstance(e, CheckoutError) for e in errors), errors
