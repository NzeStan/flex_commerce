import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from flexcommerce_core.exceptions import InsufficientFundsError, PaymentError, WalletError
from flexcommerce_orders.models import Order, Refund
from flexcommerce_orders.services import OrderService
from flexcommerce_payments import http as gateway_http_module
from flexcommerce_payments.gateways import from_minor_units, get_gateway, to_minor_units
from flexcommerce_payments.models import Payment, PaymentWebhookLog, Wallet, WalletTransaction
from flexcommerce_payments.services import PaymentService, WalletService, expire_stale_payments, safe_callback_url

from .conftest import api, make_order

pytestmark = pytest.mark.django_db
D = Decimal


def paystack_init_ok(url="https://checkout.paystack.com/abc"):
    return 200, {"status": True, "data": {"authorization_url": url, "access_code": "AC1", "reference": "x"}}


def paystack_verify(amount_kobo, status="success", currency="NGN"):
    return 200, {
        "status": True,
        "data": {
            "id": 99,
            "status": status,
            "amount": amount_kobo,
            "currency": currency,
            "channel": "card",
            "gateway_response": "Approved",
        },
    }


def sign_paystack(body: bytes, secret="sk_test_abc"):  # noqa: S107
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


class TestHelpers:
    def test_minor_units(self):
        assert to_minor_units(D("1234.56")) == 123456
        assert from_minor_units(123456) == D("1234.56")
        assert from_minor_units(None) == D("0.00")

    def test_callback_url_protection(self):
        assert safe_callback_url("") == "https://shop.example.com/checkout/done"
        assert safe_callback_url("https://shop.example.com/thanks") == "https://shop.example.com/thanks"
        with pytest.raises(PaymentError):
            safe_callback_url("https://evil.example.org/phish")

    def test_http_client(self):
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok": true}'
        with mock.patch("urllib.request.urlopen", return_value=response):
            assert gateway_http_module.request_json("POST", "https://x", {"A": "b"}, {"k": 1}) == (200, {"ok": True})
        import urllib.error

        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with pytest.raises(gateway_http_module.GatewayHTTPError):
                gateway_http_module.request_json("GET", "https://x")
        err = urllib.error.HTTPError("https://x", 400, "Bad", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            assert gateway_http_module.request_json("GET", "https://x") == (400, {})
        assert gateway_http_module._decode(b"not json") == {"raw": "not json"}


class TestMethods:
    def test_listing_for_order(self, order, user):
        keys = [m["key"] for m in api(user).get(f"/api/payments/methods/?order_id={order.pk}").json()]
        assert keys == ["paystack", "flutterwave", "bank_transfer", "pay_on_delivery"]  # no wallet balance
        WalletService.credit(user, D("999999"), "seed")
        keys = [m["key"] for m in api(user).get(f"/api/payments/methods/?order_id={order.pk}").json()]
        assert "wallet" in keys

    def test_pod_limit_and_disabled_providers(self, user, fc):
        big = make_order(user=user, price="200000.00")
        keys = [m["key"] for m in api(user).get(f"/api/payments/methods/?order_id={big.pk}").json()]
        assert "pay_on_delivery" not in keys
        fc(PRODUCT_MODELS=["tests.Product"], BANK_TRANSFER_ACCOUNTS=[])
        general = api().get("/api/payments/methods/").json()
        assert [m["key"] for m in general] == ["pay_on_delivery"]
        assert general[0]["limit"] is None

    def test_cannot_list_for_someone_elses_order(self, order):
        assert api().get(f"/api/payments/methods/?order_id={order.pk}").status_code == 404


class TestPaystack:
    def test_full_redirect_flow(self, order, user, gateway_http):
        gateway_http["paystack"].return_value = paystack_init_ok()
        resp = api(user).post(
            "/api/payments/initiate/",
            {"provider": "paystack", "order_id": str(order.pk), "callback_url": "https://shop.example.com/pay/done"},
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["authorization_url"] == "https://checkout.paystack.com/abc" and data["status"] == "pending"
        method, url, headers, payload = gateway_http["paystack"].call_args.args
        assert url.endswith("/transaction/initialize") and headers["Authorization"] == "Bearer sk_test_abc"
        assert payload["amount"] == to_minor_units(order.grand_total) and payload["email"] == "tunde@example.com"
        assert payload["callback_url"] == "https://shop.example.com/pay/done"

        gateway_http["paystack"].return_value = paystack_verify(to_minor_units(order.grand_total))
        verified = api().get(f"/api/payments/verify/?reference={data['reference']}").json()
        assert verified["status"] == "success"
        order.refresh_from_db()
        assert order.payment_status == Order.PAYMENT_PAID and order.status == Order.STATUS_CONFIRMED
        assert order.payment_provider == "paystack" and order.payment_reference == data["reference"]
        calls = gateway_http["paystack"].call_count
        api().get(f"/api/payments/verify/?trxref={data['reference']}")  # idempotent, no new API call
        assert gateway_http["paystack"].call_count == calls

    def test_underpayment_is_rejected(self, order, gateway_http):
        gateway_http["paystack"].return_value = paystack_init_ok()
        payment = PaymentService.start(order, "paystack")
        gateway_http["paystack"].return_value = paystack_verify(100)  # ₦1 instead of the order total
        payment = PaymentService.verify(payment.reference)
        order.refresh_from_db()
        assert payment.status == Payment.STATUS_FAILED and "Amount mismatch" in payment.failure_reason
        assert order.payment_status == Order.PAYMENT_FAILED and order.amount_paid == 0

    def test_currency_mismatch_rejected(self, order, gateway_http):
        gateway_http["paystack"].return_value = paystack_init_ok()
        payment = PaymentService.start(order, "paystack")
        gateway_http["paystack"].return_value = paystack_verify(to_minor_units(order.grand_total), currency="USD")
        assert PaymentService.verify(payment.reference).status == Payment.STATUS_FAILED

    def test_pending_and_failed_statuses(self, order, gateway_http):
        gateway_http["paystack"].return_value = paystack_init_ok()
        payment = PaymentService.start(order, "paystack")
        gateway_http["paystack"].return_value = paystack_verify(0, status="ongoing")
        assert PaymentService.verify(payment.reference).status == Payment.STATUS_PENDING
        gateway_http["paystack"].return_value = (503, {"status": False, "message": "down"})
        assert PaymentService.verify(payment.reference).status == Payment.STATUS_PENDING
        gateway_http["paystack"].return_value = paystack_verify(0, status="abandoned")
        assert PaymentService.verify(payment.reference).status == Payment.STATUS_FAILED

    def test_init_errors(self, order, gateway_http):
        gateway_http["paystack"].return_value = (401, {"status": False, "message": "Invalid key"})
        with pytest.raises(PaymentError) as exc:
            PaymentService.start(order, "paystack")
        assert "Invalid key" in exc.value.message
        gateway_http["paystack"].side_effect = gateway_http_module.GatewayHTTPError("timeout")
        with pytest.raises(PaymentError):
            PaymentService.start(order, "paystack")
        assert Payment.objects.filter(status=Payment.STATUS_FAILED).count() == 2

    def test_webhook(self, order, gateway_http, client):
        gateway_http["paystack"].return_value = paystack_init_ok()
        payment = PaymentService.start(order, "paystack")
        body = json.dumps(
            {
                "event": "charge.success",
                "data": {"id": 5, "reference": payment.reference, "status": "success", "amount": 1},
            }
        ).encode()
        bad = client.post(
            "/api/payments/webhooks/paystack/",
            body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE="forged",
        )
        assert bad.status_code == 401
        assert PaymentWebhookLog.objects.get().signature_valid is False
        # webhook body says amount=1 kobo but we re-verify with the API (which says full amount)
        gateway_http["paystack"].return_value = paystack_verify(to_minor_units(order.grand_total))
        ok = client.post(
            "/api/payments/webhooks/paystack/",
            body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=sign_paystack(body),
        )
        assert ok.status_code == 200 and ok.json() == {"received": True, "processed": True}
        order.refresh_from_db()
        assert order.is_paid
        again = client.post(
            "/api/payments/webhooks/paystack/",
            body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=sign_paystack(body),
        )
        assert again.json()["processed"] is False  # duplicate event ignored

    def test_webhook_unknown_reference_and_provider(self, client):
        body = json.dumps({"event": "charge.success", "data": {"id": 1, "reference": "NOPE"}}).encode()
        resp = client.post(
            "/api/payments/webhooks/paystack/",
            body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=sign_paystack(body),
        )
        assert resp.status_code == 200 and PaymentWebhookLog.objects.get().error == "Unknown reference"
        assert client.post("/api/payments/webhooks/stripe/", b"{}", content_type="application/json").status_code == 404

    def test_refund_through_gateway(self, order, gateway_http):
        gateway_http["paystack"].return_value = paystack_init_ok()
        payment = PaymentService.start(order, "paystack")
        gateway_http["paystack"].return_value = paystack_verify(to_minor_units(order.grand_total))
        PaymentService.verify(payment.reference)
        order.refresh_from_db()
        refund = OrderService(order).create_refund(D("5000"), "damaged")
        gateway_http["paystack"].return_value = (200, {"status": True, "data": {"id": 777}})
        OrderService(order).process_refund(refund)
        refund.refresh_from_db()
        assert refund.status == Refund.STATUS_PROCESSED and refund.reference == "777"
        assert gateway_http["paystack"].call_args.args[3] == {
            "transaction": payment.reference,
            "amount": 500000,
            "merchant_note": "damaged",
        }
        failing = OrderService(order).create_refund(D("100"), "x")
        gateway_http["paystack"].return_value = (400, {"status": False, "message": "Insufficient balance"})
        OrderService(order).process_refund(failing)
        failing.refresh_from_db()
        assert failing.status == Refund.STATUS_FAILED and failing.failure_reason == "Insufficient balance"
        network = OrderService(order).create_refund(D("100"), "x")
        gateway_http["paystack"].side_effect = gateway_http_module.GatewayHTTPError("timeout")
        OrderService(order).process_refund(network)
        network.refresh_from_db()
        assert network.status == Refund.STATUS_FAILED


class TestFlutterwave:
    def test_flow_webhook_and_refund(self, order, gateway_http, client):
        flw = gateway_http["flutterwave"]
        flw.return_value = (200, {"status": "success", "data": {"link": "https://checkout.flutterwave.com/x"}})
        payment = PaymentService.start(order, "flutterwave")
        assert payment.authorization_url == "https://checkout.flutterwave.com/x"
        assert flw.call_args.args[3]["tx_ref"] == payment.reference
        body = json.dumps(
            {"event": "charge.completed", "data": {"id": 42, "tx_ref": payment.reference, "status": "successful"}}
        ).encode()
        assert (
            client.post(
                "/api/payments/webhooks/flutterwave/", body, content_type="application/json", HTTP_VERIF_HASH="wrong"
            ).status_code
            == 401
        )
        flw.return_value = (
            200,
            {
                "status": "success",
                "data": {
                    "id": 42,
                    "status": "successful",
                    "amount": float(order.grand_total),
                    "currency": "NGN",
                    "payment_type": "card",
                },
            },
        )
        ok = client.post(
            "/api/payments/webhooks/flutterwave/", body, content_type="application/json", HTTP_VERIF_HASH="flw-hash"
        )
        assert ok.json()["processed"] is True
        payment.refresh_from_db()
        assert payment.status == Payment.STATUS_SUCCESS and payment.provider_reference == "42"
        order.refresh_from_db()
        refund = OrderService(order).create_refund(D("1000"), "x")
        flw.return_value = (200, {"status": "success", "data": {"id": 9}})
        OrderService(order).process_refund(refund)
        refund.refresh_from_db()
        assert refund.status == Refund.STATUS_PROCESSED and flw.call_args.args[1].endswith("/transactions/42/refund")

    def test_failures(self, order, gateway_http):
        flw = gateway_http["flutterwave"]
        flw.return_value = (400, {"status": "error", "message": "bad"})
        with pytest.raises(PaymentError):
            PaymentService.start(order, "flutterwave")
        gateway = get_gateway("flutterwave")
        payment = Payment(reference="R", amount=D("1"), provider_reference="")
        assert gateway.refund(payment, D("1"))["success"] is False
        payment.provider_reference = "1"
        assert gateway.refund(payment, D("1"))["success"] is False
        flw.return_value = (200, {"status": "success", "data": {"status": "failed", "processor_response": "Declined"}})
        assert gateway.verify(payment).status == "failed"
        flw.return_value = (500, {})
        assert gateway.verify(payment).status == "pending"


class TestOfflineAndGuards:
    def test_bank_transfer_instructions(self, order, user):
        data = (
            api(user).post("/api/payments/initiate/", {"provider": "bank_transfer", "order_id": str(order.pk)}).json()
        )
        assert data["status"] == "pending"
        assert data["instructions"]["accounts"][0]["bank_name"] == "GTBank"
        assert data["instructions"]["narration"] == order.order_number
        assert PaymentService.verify(data["reference"]).status == Payment.STATUS_PENDING

    def test_guards(self, order, user, gateway_http):
        with pytest.raises(PaymentError) as exc:
            PaymentService.start(order, "stripe")
        assert exc.value.code == "unknown_payment_method"
        with pytest.raises(PaymentError) as exc:
            PaymentService.start(order, "wallet")
        assert exc.value.code == "payment_method_unavailable"
        OrderService(order).mark_paid(reference="cash")
        order.refresh_from_db()
        with pytest.raises(PaymentError) as exc:
            PaymentService.start(order, "paystack")
        assert exc.value.code == "order_already_paid"
        cancelled = make_order(user=user)
        OrderService(cancelled).cancel()
        with pytest.raises(PaymentError) as exc:
            PaymentService.start(cancelled, "paystack")
        assert exc.value.code == "order_not_payable"

    def test_guest_payment_needs_token(self, gateway_http):
        order = make_order(email="guest@example.com")
        gateway_http["paystack"].return_value = paystack_init_ok()
        payload = {"provider": "paystack", "order_number": order.order_number}
        assert api().post("/api/payments/initiate/", payload).status_code == 404
        assert api().post("/api/payments/initiate/", {**payload, "token": "wrong"}).status_code == 404
        resp = api().post("/api/payments/initiate/", {**payload, "token": order.access_token})
        assert resp.status_code == 201

    def test_other_users_cannot_pay_or_see(self, order, staff, gateway_http):
        from django.contrib.auth import get_user_model

        intruder = get_user_model().objects.create_user("x", "x@x.com", "x")
        assert (
            api(intruder)
            .post("/api/payments/initiate/", {"provider": "paystack", "order_id": str(order.pk)})
            .status_code
            == 404
        )
        assert api().post("/api/payments/initiate/", {"provider": "paystack"}).status_code == 400
        gateway_http["paystack"].return_value = paystack_init_ok()
        PaymentService.start(order, "paystack")
        assert api(intruder).get("/api/payments/").json()["count"] == 0
        assert api(staff).get("/api/payments/?status=pending").json()["count"] == 1
        assert api().get("/api/payments/verify/").status_code == 400
        assert api().get("/api/payments/verify/?reference=NOPE").status_code == 404

    def test_evil_callback_rejected(self, order, user):
        resp = api(user).post(
            "/api/payments/initiate/",
            {"provider": "paystack", "order_id": str(order.pk), "callback_url": "https://evil.example.org/"},
        )
        assert resp.status_code == 402 and resp.json()["error"] == "invalid_callback_url"


class TestWallet:
    def test_credit_debit_idempotent_and_safe(self, user):
        WalletService.credit(user, D("5000"), "ref-1", source="topup")
        WalletService.credit(user, D("5000"), "ref-1", source="topup")  # duplicate ignored
        wallet = Wallet.objects.get(user=user)
        assert wallet.balance == D("5000.00") and "Wallet(" in str(wallet)
        txn = WalletService.debit(user, D("2000"), "ref-2")
        assert txn.balance_after == D("3000.00") and str(txn).startswith("-2000")
        with pytest.raises(InsufficientFundsError) as exc:
            WalletService.debit(user, D("3000.01"), "ref-3")
        assert exc.value.extra == {"balance": "3000.00", "required": "3000.01"}
        with pytest.raises(WalletError):
            WalletService.credit(user, D("0"), "zero")
        with pytest.raises(WalletError):
            WalletService.debit(user, D("-1"), "neg")

    def test_debit_without_wallet_and_limits(self, user, fc):
        with pytest.raises(InsufficientFundsError):
            WalletService.debit(user, D("1"), "none")
        fc(PRODUCT_MODELS=["tests.Product"], WALLET_MAX_BALANCE=1000)
        WalletService.credit(user, D("900"), "t1", source="topup")
        with pytest.raises(WalletError):
            WalletService.credit(user, D("200"), "t2", source="topup")
        WalletService.credit(user, D("200"), "refund-1", source="refund")  # refunds are never blocked

    def test_pay_order_with_wallet(self, order, user):
        WalletService.credit(user, D("100000"), "seed")
        data = api(user).post("/api/payments/initiate/", {"provider": "wallet", "order_id": str(order.pk)}).json()
        assert data["status"] == "success"
        order.refresh_from_db()
        assert order.is_paid and order.status == Order.STATUS_CONFIRMED
        assert Wallet.objects.get(user=user).balance == D("100000") - order.grand_total
        payment = Payment.objects.get(reference=data["reference"])
        assert get_gateway("wallet").verify(payment).status == "success"

    def test_refund_to_wallet_and_original_wallet_payment(self, order, user):
        WalletService.credit(user, order.grand_total, "seed")
        PaymentService.start(order, "wallet", user=user)
        order.refresh_from_db()
        refund = OrderService(order).create_refund(D("1000"), "sorry", method=Refund.METHOD_WALLET)
        OrderService(order).process_refund(refund)
        assert Wallet.objects.get(user=user).balance == D("1000.00")
        original = OrderService(order).create_refund(D("500"), "again")
        OrderService(order).process_refund(original)
        assert Wallet.objects.get(user=user).balance == D("1500.00")

    def test_guest_wallet_refund_fails(self, gateway_http):
        order = make_order(email="guest@example.com")
        OrderService(order).mark_paid(reference="cash")
        order.refresh_from_db()
        refund = OrderService(order).create_refund(D("100"), "x", method=Refund.METHOD_WALLET)
        OrderService(order).process_refund(refund)
        refund.refresh_from_db()
        assert refund.status == Refund.STATUS_FAILED

    def test_offline_payment_refunds_are_manual(self, order):
        OrderService(order).mark_paid(reference="bank")
        order.refresh_from_db()
        refund = OrderService(order).create_refund(D("100"), "x")
        OrderService(order).process_refund(refund)
        refund.refresh_from_db()
        assert refund.status == Refund.STATUS_PROCESSED and refund.reference.startswith("MANUAL-")

    def test_topup_api(self, user, gateway_http):
        gateway_http["paystack"].return_value = paystack_init_ok()
        c = api(user)
        assert c.post("/api/wallet/topup/", {"amount": "50", "provider": "paystack"}).status_code == 400
        assert c.post("/api/wallet/topup/", {"amount": "5000", "provider": "nope"}).status_code == 402
        assert c.post("/api/wallet/topup/", {"amount": "5000", "provider": "bank_transfer"}).status_code == 402
        data = c.post("/api/wallet/topup/", {"amount": "5000", "provider": "paystack"}).json()
        gateway_http["paystack"].return_value = paystack_verify(500000)
        PaymentService.verify(data["reference"])
        assert c.get("/api/wallet/").json()["balance"] == "5000.00"
        txns = c.get("/api/wallet/transactions/").json()
        assert txns["count"] == 1 and txns["results"][0]["source"] == "topup"

    def test_staff_adjust(self, user, staff):
        s = api(staff)
        assert api(user).post("/api/wallet/adjust/", {}).status_code == 403
        ok = s.post("/api/wallet/adjust/", {"user_id": str(user.pk), "amount": "2500", "description": "Goodwill"})
        assert ok.status_code == 201 and ok.json()["balance_after"] == "2500.00"
        down = s.post("/api/wallet/adjust/", {"user_id": str(user.pk), "amount": "-500", "description": "Fix"})
        assert down.json()["balance_after"] == "2000.00"
        assert (
            s.post("/api/wallet/adjust/", {"user_id": str(user.pk), "amount": "0", "description": "x"}).status_code
            == 400
        )
        assert s.post("/api/wallet/adjust/", {"user_id": "99999", "amount": "1", "description": "x"}).status_code == 404


class TestJobsAndInfra:
    def test_expire_stale(self, order, gateway_http):
        gateway_http["paystack"].return_value = paystack_init_ok()
        payment = PaymentService.start(order, "paystack")
        Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(days=2))
        gateway_http["paystack"].return_value = paystack_verify(0, status="ongoing")
        assert expire_stale_payments() == {"abandoned": 1}
        payment.refresh_from_db()
        assert payment.status == Payment.STATUS_ABANDONED

    def test_migrations_and_admin(self, client, order, user, gateway_http):
        from django.contrib.auth import get_user_model

        call_command("makemigrations", "flexcommerce_payments", "--check", "--dry-run", stdout=StringIO())
        gateway_http["paystack"].return_value = paystack_init_ok()
        payment = PaymentService.start(order, "paystack")
        WalletService.credit(user, D("10"), "seed")
        PaymentWebhookLog.objects.create(provider="paystack", event_type="charge.success")
        admin = get_user_model().objects.create_superuser("root", "r@x.com", "x")
        client.force_login(admin)
        for m in ("payment", "paymentwebhooklog", "wallet"):
            assert client.get(f"/admin/flexcommerce_payments/{m}/").status_code == 200
        wallet = Wallet.objects.get(user=user)
        assert client.get(f"/admin/flexcommerce_payments/wallet/{wallet.pk}/change/").status_code == 200
        gateway_http["paystack"].return_value = (500, {})
        client.post(
            "/admin/flexcommerce_payments/payment/", {"action": "reverify", "_selected_action": [str(payment.pk)]}
        )
        assert str(payment).startswith("paystack") and str(PaymentWebhookLog.objects.first()).startswith("paystack")
        assert WalletTransaction.objects.count() == 1
