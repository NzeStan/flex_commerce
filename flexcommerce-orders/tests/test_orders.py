from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone

from flexcommerce_core import hooks
from flexcommerce_core.exceptions import (
    InvalidOrderTransitionError,
    OrderCancellationError,
    OrderError,
    RefundError,
    ReturnError,
)
from flexcommerce_core.models import AuditLog
from flexcommerce_orders import signals
from flexcommerce_orders.models import Order, Refund, ReturnRequest, Shipment
from flexcommerce_orders.services import (
    OrderService,
    expire_unpaid_orders,
    find_guest_order,
    generate_order_number,
    order_payload,
)

from .conftest import client_for, make_order

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def signal_log():
    log = []
    names = [n for n in dir(signals) if not n.startswith("_") and n not in ("Signal",)]
    for name in names:
        getattr(signals, name).connect(
            lambda sender, _n=name, **kw: log.append(_n), weak=False, dispatch_uid=f"t-{name}"
        )
    yield log
    for name in names:
        getattr(signals, name).disconnect(dispatch_uid=f"t-{name}")


def ship_all(order, **kwargs):
    return OrderService(order).create_shipment(carrier="GIG Logistics", tracking_number="GIG123", **kwargs)


def deliver(order):
    shipment = ship_all(order)
    OrderService(order).update_shipment(shipment, Shipment.STATUS_DELIVERED)
    order.refresh_from_db()
    return order


class TestPlacement:
    def test_place_order_snapshots(self, order, user):
        assert order.order_number.startswith("FC") and len(order.order_number) == 16
        assert order.email == "ada@example.com" and order.user == user
        assert order.grand_total == D("26500.00") and order.balance_due == D("26500.00")
        item = order.items.first()
        assert item.product_name == "P0" and item.product_data == {"image": "https://cdn.example.com/SKU0.jpg"}
        assert item.paid_line_total == D("20000.00") and item.unit_paid == D("10000.00")
        assert order.events.get().event == "placed"
        assert AuditLog.objects.filter(action="create").exists()
        assert order.customer_name == "Ada Obi" and str(order).startswith("Order #FC")

    def test_guest_order(self):
        order = make_order(email="Guest@Example.com")
        assert order.user is None and order.email == "guest@example.com" and order.customer_name == "Chioma Eze"
        assert find_guest_order(order.order_number, "GUEST@example.com") == order
        assert find_guest_order(order.order_number, "wrong@example.com") is None
        assert find_guest_order("", "x") is None

    def test_created_hook_and_signal(self, user, signal_log):
        seen = []
        hooks.register("order.created", lambda order, lines, **kw: seen.append(len(lines)))
        try:
            make_order(user=user)
        finally:
            hooks._registry["order.created"].clear()
        assert seen == [2] and "order_created" in signal_log

    def test_idempotency_key_unique(self, user):
        make_order(user=user, idempotency_key="key-1")
        with pytest.raises(IntegrityError):
            make_order(user=user, idempotency_key="key-1")

    def test_custom_order_number_generator(self, fc):
        fc(
            PRODUCT_MODELS=["tests.Product"],
            ORDER_NUMBER_GENERATOR="tests.test_orders.fixed_number",
        )
        assert generate_order_number() == "CUSTOM-1"

    def test_order_number_collision_retry(self, user, monkeypatch):
        first = make_order(user=user)
        numbers = iter([first.order_number, "FC-FRESH-1"])
        monkeypatch.setattr("flexcommerce_orders.services.generate_order_number", lambda: next(numbers))
        assert make_order(user=user).order_number == "FC-FRESH-1"

    def test_payload(self, order):
        payload = order_payload(order)
        assert payload["order_number"] == order.order_number and len(payload["items"]) == 2


def fixed_number():
    return "CUSTOM-1"


class TestPaymentAndConfirmation:
    def test_mark_paid_confirms(self, order, signal_log):
        OrderService(order).mark_paid(reference="PSK-1", provider="paystack")
        order.refresh_from_db()
        assert order.payment_status == Order.PAYMENT_PAID and order.status == Order.STATUS_CONFIRMED
        assert order.amount_paid == order.grand_total and order.paid_at and order.confirmed_at
        assert signal_log.count("order_paid") == 1 and "order_confirmed" in signal_log
        # idempotent for the same reference (webhook + redirect verification)
        OrderService(order).mark_paid(reference="PSK-1")
        order.refresh_from_db()
        assert order.amount_paid == order.grand_total and signal_log.count("order_paid") == 1

    def test_partial_payments(self, order):
        OrderService(order).mark_paid(amount=D("20000"), reference="A")
        order.refresh_from_db()
        assert order.payment_status == Order.PAYMENT_PENDING and order.status == Order.STATUS_PENDING
        OrderService(order).mark_paid(amount=D("6500"), reference="B")
        order.refresh_from_db()
        assert order.is_paid and order.status == Order.STATUS_CONFIRMED
        OrderService(order).mark_paid(amount=D("0"))  # no-op

    def test_payment_failed(self, order, signal_log):
        OrderService(order).mark_payment_failed(reason="declined")
        order.refresh_from_db()
        assert order.payment_status == Order.PAYMENT_FAILED and "order_payment_failed" in signal_log
        OrderService(order).mark_paid(reference="retry")
        order.refresh_from_db()
        assert order.is_paid
        OrderService(order).mark_payment_failed()  # ignored once paid
        order.refresh_from_db()
        assert order.is_paid

    def test_late_payment_after_cancel_creates_refund(self, order):
        OrderService(order).cancel(reason="timeout")
        OrderService(order).mark_paid(reference="LATE")
        order.refresh_from_db()
        assert order.status == Order.STATUS_CANCELLED
        refund = order.refunds.get()
        assert refund.amount == order.grand_total and refund.status == Refund.STATUS_REQUESTED

    def test_confirm_is_idempotent(self, order):
        OrderService(order).confirm()
        OrderService(order).confirm()
        assert order.events.filter(event="confirmed").count() == 1


class TestCancellation:
    def test_customer_cancel_rules(self, order, fc):
        OrderService(order).confirm()
        OrderService(order).transition(Order.STATUS_PROCESSING)
        with pytest.raises(OrderCancellationError):
            OrderService(order).cancel(by_customer=True)
        OrderService(order).cancel(actor="staff")  # staff may
        order.refresh_from_db()
        assert order.status == Order.STATUS_CANCELLED and order.cancelled_at

    def test_cannot_cancel_shipped(self, order):
        OrderService(order).confirm()
        ship_all(order)
        with pytest.raises(OrderCancellationError):
            OrderService(order).cancel()

    def test_cancel_paid_order_creates_refund(self, order, fc):
        OrderService(order).mark_paid(reference="X")
        OrderService(order).cancel(reason="changed mind", by_customer=True)
        refund = order.refunds.get()
        assert refund.amount == D("26500.00") and refund.status == Refund.STATUS_REQUESTED

    def test_auto_process_cancellation_refund(self, order, fc):
        fc(PRODUCT_MODELS=["tests.Product"], AUTO_PROCESS_CANCELLATION_REFUNDS=True)
        OrderService(order).mark_paid(reference="X")
        OrderService(order).cancel()
        order.refresh_from_db()
        assert order.payment_status == Order.PAYMENT_REFUNDED and order.amount_refunded == order.grand_total

    def test_cancel_hook(self, order):
        seen = []

        def hook(order, actor=None, **kw):
            seen.append(order.order_number)

        hooks.register("order.cancelled", hook)
        try:
            OrderService(order).cancel()
        finally:
            hooks.unregister("order.cancelled", hook)
        assert seen == [order.order_number]

    def test_expire_unpaid(self, user):
        stale = make_order(user=user, payment_due_at=timezone.now() - timedelta(minutes=1))
        fresh = make_order(user=user, payment_due_at=timezone.now() + timedelta(minutes=30))
        pod = make_order(user=user, payment_method=Order.PAYMENT_METHOD_POD)
        assert expire_unpaid_orders() == {"cancelled": 1}
        stale.refresh_from_db()
        fresh.refresh_from_db()
        pod.refresh_from_db()
        assert stale.status == Order.STATUS_CANCELLED and "not received" in stale.cancellation_reason
        assert fresh.status == pod.status == Order.STATUS_PENDING

    def test_expire_survives_errors(self, user, monkeypatch):
        make_order(user=user, payment_due_at=timezone.now() - timedelta(minutes=1))
        monkeypatch.setattr(OrderService, "cancel", lambda self, **kw: 1 / 0)
        assert expire_unpaid_orders() == {"cancelled": 0}


class TestTransitions:
    def test_invalid_transition(self, order):
        with pytest.raises(InvalidOrderTransitionError) as exc:
            OrderService(order).transition(Order.STATUS_SHIPPED)
        assert exc.value.extra["allowed"] == ["confirmed", "cancelled"]
        with pytest.raises(InvalidOrderTransitionError):
            OrderService(order).transition(Order.STATUS_REFUNDED)

    def test_model_transition_helper(self, order):
        order.transition(Order.STATUS_CONFIRMED, actor="admin")
        order.refresh_from_db()
        assert order.status == Order.STATUS_CONFIRMED
        order.transition(Order.STATUS_CANCELLED, actor="admin")
        assert Order.objects.get(pk=order.pk).status == Order.STATUS_CANCELLED

    def test_same_status_is_noop(self, order):
        OrderService(order).confirm()
        assert OrderService(order)._set_status(Order.STATUS_CONFIRMED) is False


class TestShipping:
    def test_partial_then_full_shipment_and_delivery(self, order, signal_log):
        OrderService(order).confirm()
        a, b = order.items.all()
        first = OrderService(order).create_shipment(
            items=[{"order_item_id": str(a.pk), "quantity": 1}], carrier="GIG", tracking_number="T1"
        )
        order.refresh_from_db()
        assert order.status == Order.STATUS_PARTIALLY_SHIPPED and first.events.count() == 1
        second = ship_all(order)
        order.refresh_from_db()
        assert order.status == Order.STATUS_SHIPPED
        assert {e["quantity"] for e in second.items} == {1}
        OrderService(order).update_shipment(first, Shipment.STATUS_IN_TRANSIT, location="Onitsha")
        OrderService(order).update_shipment(first, Shipment.STATUS_DELIVERED)
        order.refresh_from_db()
        assert order.status == Order.STATUS_SHIPPED  # second shipment still in transit
        OrderService(order).update_shipment(second, Shipment.STATUS_DELIVERED)
        order.refresh_from_db()
        assert order.status == Order.STATUS_DELIVERED and order.delivered_at
        assert {
            "shipment_created",
            "shipment_updated",
            "order_partially_shipped",
            "order_shipped",
            "order_delivered",
        } <= set(signal_log)
        assert "Onitsha" in order.events.filter(event="shipment_updated").first().message

    def test_shipment_validation(self, order):
        with pytest.raises(OrderError):
            ship_all(order)  # still pending
        OrderService(order).confirm()
        item = order.items.first()
        with pytest.raises(OrderError) as exc:
            OrderService(order).create_shipment(items=[{"order_item_id": str(item.pk), "quantity": 99}])
        assert exc.value.code == "over_shipment"
        with pytest.raises(OrderError):
            OrderService(order).create_shipment(items=[{"order_item_id": "bogus", "quantity": 1}])
        with pytest.raises(OrderError):
            OrderService(order).create_shipment(items=[{"order_item_id": str(item.pk), "quantity": 0}])
        ship_all(order)
        OrderService(order).transition(Order.STATUS_DELIVERED)
        with pytest.raises(OrderError):
            ship_all(order)

    def test_vendor_filtered_shipment(self, user):
        import uuid

        order = make_order(user=user)
        vendor = uuid.uuid4()
        order.items.filter(product_name="P0").update(vendor_id=vendor)
        OrderService(order).confirm()
        shipment = OrderService(order).create_shipment(vendor_id=vendor)
        assert len(shipment.items) == 1
        other = order.items.get(product_name="P1")
        with pytest.raises(OrderError):
            OrderService(order).create_shipment(
                items=[{"order_item_id": str(other.pk), "quantity": 1}], vendor_id=vendor
            )
        with pytest.raises(OrderError) as exc:
            OrderService(order).create_shipment(vendor_id=vendor)
        assert exc.value.code == "nothing_to_ship"

    def test_shipment_must_belong_to_order(self, user):
        a, b = make_order(user=user), make_order(user=user)
        for o in (a, b):
            OrderService(o).confirm()
        shipment = ship_all(a)
        with pytest.raises(OrderError):
            OrderService(b).update_shipment(shipment, Shipment.STATUS_DELIVERED)

    def test_mark_delivered(self, order):
        OrderService(order).confirm()
        ship_all(order)
        OrderService(order).mark_delivered()
        order.refresh_from_db()
        assert order.status == Order.STATUS_DELIVERED
        assert not order.shipments.exclude(status=Shipment.STATUS_DELIVERED).exists()


class TestRefunds:
    def paid(self, order):
        OrderService(order).mark_paid(reference="P")
        order.refresh_from_db()
        return order

    def test_refund_limits(self, order):
        with pytest.raises(RefundError):
            OrderService(order).create_refund(D("1"), "unpaid")
        self.paid(order)
        with pytest.raises(RefundError):
            OrderService(order).create_refund(D("0"), "zero")
        OrderService(order).create_refund(D("20000"), "first")
        with pytest.raises(RefundError) as exc:
            OrderService(order).create_refund(D("7000"), "too much with pending")
        assert exc.value.extra == {"refundable": "6500.00"}

    def test_process_manual_refund_after_delivery(self, order, staff, signal_log):
        self.paid(order)
        deliver(order)
        service = OrderService(order)
        partial = service.create_refund(D("6500"), "missing item", method=Refund.METHOD_MANUAL)
        service.process_refund(partial, processed_by=staff)
        order.refresh_from_db()
        partial.refresh_from_db()
        assert partial.status == Refund.STATUS_PROCESSED and partial.processed_by == staff
        assert partial.reference.startswith("MANUAL-")
        assert order.status == Order.STATUS_PARTIALLY_REFUNDED
        assert order.payment_status == Order.PAYMENT_PARTIALLY_REFUNDED
        rest = service.create_refund(order.refundable_amount, "rest")
        service.process_refund(rest)
        order.refresh_from_db()
        assert order.status == Order.STATUS_REFUNDED and order.payment_status == Order.PAYMENT_REFUNDED
        with pytest.raises(RefundError):
            service.process_refund(rest)
        assert "refund_processed" in signal_log and "order_refunded" in signal_log

    def test_gateway_hook_success_and_failure(self, order, signal_log):
        self.paid(order)
        results = iter([{"success": False, "error": "gateway down"}, {"success": True, "reference": "RF-9"}])

        def gateway(refund, order, **kw):
            return next(results)

        hooks.register("refund.process", gateway)
        try:
            refund = OrderService(order).create_refund(D("1000"), "x")
            OrderService(order).process_refund(refund)
            refund.refresh_from_db()
            assert refund.status == Refund.STATUS_FAILED and refund.failure_reason == "gateway down"
            assert "refund_failed" in signal_log
            again = OrderService(order).create_refund(D("1000"), "retry")
            OrderService(order).process_refund(again)
            again.refresh_from_db()
            assert again.status == Refund.STATUS_PROCESSED and again.reference == "RF-9"
        finally:
            hooks.unregister("refund.process", gateway)

    def test_reject_refund(self, order):
        self.paid(order)
        refund = OrderService(order).create_refund(D("100"), "x")
        OrderService(order).reject_refund(refund, reason="not eligible")
        refund.refresh_from_db()
        assert refund.status == Refund.STATUS_REJECTED
        with pytest.raises(RefundError):
            OrderService(order).reject_refund(refund)

    def test_process_rejects_when_amount_now_exceeds_paid(self, order):
        self.paid(order)
        refund = OrderService(order).create_refund(D("100"), "x")
        Order.objects.filter(pk=order.pk).update(amount_refunded=order.amount_paid)
        with pytest.raises(RefundError):
            OrderService(order).process_refund(refund)


class TestReturns:
    def delivered(self, order):
        OrderService(order).mark_paid(reference="P")
        return deliver(order)

    def test_full_return_flow(self, order, user, staff):
        self.delivered(order)
        item = order.items.get(product_name="P0")
        service = OrderService(order)
        rr = service.request_return(
            [{"order_item_id": str(item.pk), "quantity": 1}],
            reason_code="damaged",
            reason="Screen cracked",
            user=user,
        )
        assert rr.status == ReturnRequest.STATUS_PENDING and rr.user == user
        with pytest.raises(ReturnError) as exc:
            service.request_return([{"order_item_id": str(item.pk), "quantity": 2}])
        assert exc.value.extra["returnable"] == 1  # one already pending
        service.approve_return(rr, approved_by=staff)
        rr, refund = service.receive_return(rr)
        assert rr.status == ReturnRequest.STATUS_RECEIVED and refund.amount == D("10000.00")
        assert refund.status == Refund.STATUS_APPROVED and refund.return_request == rr
        item.refresh_from_db()
        assert item.quantity_returned == 1
        service.process_refund(refund)
        rr.refresh_from_db()
        order.refresh_from_db()
        assert rr.status == ReturnRequest.STATUS_REFUNDED and order.status == Order.STATUS_PARTIALLY_REFUNDED
        # can still return the other unit of the same line
        service.request_return([{"order_item_id": str(item.pk), "quantity": 1}])

    def test_return_refund_uses_paid_amount_after_discount(self, user):
        order = make_order(user=user, qtys=(2,), prices=("10000",), discount=D("2000"))
        OrderService(order).mark_paid(reference="P")
        deliver(order)
        item = order.items.get()
        service = OrderService(order)
        rr = service.request_return([{"order_item_id": str(item.pk), "quantity": 1}])
        service.approve_return(rr)
        _, refund = service.receive_return(rr)
        assert refund.amount == D("9000.00")  # (20000 - 2000) / 2

    def test_return_rules(self, order, fc):
        item = order.items.first()
        entry = [{"order_item_id": str(item.pk), "quantity": 1}]
        with pytest.raises(ReturnError) as exc:
            OrderService(order).request_return(entry)
        assert exc.value.code == "not_delivered"
        self.delivered(order)
        Order.objects.filter(pk=order.pk).update(delivered_at=timezone.now() - timedelta(days=30))
        with pytest.raises(ReturnError) as exc:
            OrderService(order).request_return(entry)
        assert exc.value.code == "return_window_closed"
        fc(PRODUCT_MODELS=["tests.Product"], RETURN_WINDOW_DAYS=0)
        with pytest.raises(ReturnError) as exc:
            OrderService(order).request_return(entry)
        assert exc.value.code == "returns_disabled"

    def test_reject_and_state_guards(self, order):
        self.delivered(order)
        item = order.items.first()
        service = OrderService(order)
        rr = service.request_return([{"order_item_id": str(item.pk), "quantity": 1}])
        with pytest.raises(ReturnError):
            service.receive_return(rr)  # not approved yet
        service.reject_return(rr, note="used item")
        with pytest.raises(ReturnError):
            service.approve_return(rr)
        with pytest.raises(ReturnError):
            service.reject_return(rr)

    def test_receive_without_refund(self, order):
        self.delivered(order)
        item = order.items.first()
        service = OrderService(order)
        rr = service.request_return([{"order_item_id": str(item.pk), "quantity": 1}])
        service.approve_return(rr)
        rr, refund = service.receive_return(rr, refund=False, restock=False)
        assert refund is None


class TestCustomerAPI:
    def test_list_detail_privacy(self, order, user, other_user):
        mine = client_for(user)
        data = mine.get("/api/orders/").json()
        assert data["count"] == 1 and data["results"][0]["item_count"] == 3
        assert mine.get("/api/orders/my-orders/").json()["count"] == 1
        detail = mine.get(f"/api/orders/{order.pk}/").json()
        assert detail["timeline"][0]["message"] == "Your order has been placed." and detail["can_cancel"] is True
        assert "internal_note" not in detail
        assert client_for(other_user).get(f"/api/orders/{order.pk}/").status_code == 404
        assert client_for().get("/api/orders/").status_code in (401, 403)

    def test_customer_cancel_and_return(self, order, user):
        mine = client_for(user)
        resp = mine.post(f"/api/orders/{order.pk}/cancel/", {"reason": "found it cheaper"}, format="json")
        assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
        other = make_order(user=user)
        OrderService(other).mark_paid(reference="Z")
        deliver(other)
        item = other.items.first()
        resp = mine.post(
            f"/api/orders/{other.pk}/return/",
            {
                "items": [{"order_item_id": str(item.pk), "quantity": 1}],
                "reason_code": "wrong_item",
            },
            format="json",
        )
        assert resp.status_code == 201 and resp.json()["order_number"] == other.order_number
        assert mine.get("/api/returns/").json()["count"] == 1
        assert mine.post(f"/api/orders/{other.pk}/cancel/").status_code == 409

    def test_customers_cannot_use_staff_actions(self, order, user):
        mine = client_for(user)
        assert mine.post(f"/api/orders/{order.pk}/transition/", {"to_state": "confirmed"}).status_code == 403
        assert mine.post(f"/api/orders/{order.pk}/mark-paid/").status_code == 403
        assert mine.post(f"/api/orders/{order.pk}/shipments/").status_code == 403
        assert mine.post(f"/api/orders/{order.pk}/refund/", {"amount": "1", "reason": "x"}).status_code == 403

    def test_guest_tracking(self):
        order = make_order(email="guest@example.com")
        anon = client_for()
        resp = anon.post("/api/orders/track/", {"order_number": order.order_number, "email": "GUEST@example.com"})
        assert resp.status_code == 200 and resp.json()["access_token"] == order.access_token
        assert (
            anon.post("/api/orders/track/", {"order_number": order.order_number, "email": "x@y.com"}).status_code == 404
        )
        ok = anon.get(f"/api/orders/guest/{order.order_number}/?token={order.access_token}")
        assert ok.status_code == 200 and ok.json()["order_number"] == order.order_number
        assert anon.get(f"/api/orders/guest/{order.order_number}/?token=nope").status_code == 404
        assert anon.get(f"/api/orders/guest/{order.order_number}/").status_code == 404

    def test_guest_tracking_throttled(self, fc):
        fc(PRODUCT_MODELS=["tests.Product"], THROTTLE_RATES={"guest_order_lookup": "2/minute"})
        anon = client_for()
        codes = [
            anon.post("/api/orders/track/", {"order_number": "X", "email": "a@b.com"}).status_code for _ in range(3)
        ]
        assert codes == [404, 404, 429]


class TestStaffAPI:
    def test_full_staff_workflow(self, order, staff):
        s = client_for(staff)
        assert s.get("/api/orders/?search=" + order.order_number).json()["count"] == 1
        assert s.get("/api/orders/?payment_status=paid").json()["count"] == 0
        assert (
            s.get(f"/api/orders/?from_date={timezone.now().date()}&to_date={timezone.now().date()}").json()["count"]
            == 1
        )
        data = s.post(f"/api/orders/{order.pk}/mark-paid/", {"reference": "BANK-001"}, format="json").json()
        assert data["payment_status"] == "paid" and data["status"] == "confirmed" and "all_events" in data
        data = s.post(f"/api/orders/{order.pk}/transition/", {"to_state": "processing"}, format="json").json()
        assert data["status"] == "processing"
        assert s.post(f"/api/orders/{order.pk}/transition/", {"to_state": "pending"}, format="json").status_code == 409
        shipment = s.post(
            f"/api/orders/{order.pk}/shipments/",
            {"carrier": "DHL", "tracking_number": "D1", "tracking_url": "https://dhl.com/t/D1"},
            format="json",
        )
        assert shipment.status_code == 201
        sid = shipment.json()["id"]
        ev = s.post(
            f"/api/orders/{order.pk}/shipments/{sid}/events/",
            {"status": "delivered", "location": "Enugu"},
            format="json",
        )
        assert ev.status_code == 201 and ev.json()["status"] == "delivered"
        assert (
            s.post(
                f"/api/orders/{order.pk}/shipments/00000000-0000-0000-0000-000000000000/events/",
                {"status": "delivered"},
                format="json",
            ).status_code
            == 404
        )
        refund = s.post(
            f"/api/orders/{order.pk}/refund/",
            {"amount": "1000", "reason": "goodwill", "method": "manual", "process": True},
            format="json",
        )
        assert refund.status_code == 201 and refund.json()["status"] == "processed"
        pending = s.post(f"/api/orders/{order.pk}/refund/", {"amount": "500", "reason": "x"}, format="json").json()
        assert (
            s.post(f"/api/orders/{order.pk}/refunds/{pending['id']}/reject/", {"reason": "no"}).json()["status"]
            == "rejected"
        )
        another = s.post(
            f"/api/orders/{order.pk}/refund/",
            {"amount": "500", "reason": "y", "method": "manual"},
            format="json",
        ).json()
        assert s.post(f"/api/orders/{order.pk}/approve-refund/{another['id']}/").json()["status"] == "processed"
        assert (
            s.post(f"/api/orders/{order.pk}/refunds/00000000-0000-0000-0000-000000000000/process/").status_code == 404
        )

    def test_deliver_and_cancel_endpoints(self, user, staff):
        s = client_for(staff)
        order = make_order(user=user)
        s.post(f"/api/orders/{order.pk}/transition/", {"to_state": "confirmed"}, format="json")
        s.post(f"/api/orders/{order.pk}/shipments/", {}, format="json")
        assert s.post(f"/api/orders/{order.pk}/deliver/").json()["status"] == "delivered"
        other = make_order(user=user)
        OrderService(other).confirm()
        OrderService(other).transition(Order.STATUS_PROCESSING)
        assert s.post(f"/api/orders/{other.pk}/cancel/").json()["status"] == "cancelled"  # staff bypass

    def test_return_decisions(self, order, user, staff):
        OrderService(order).mark_paid(reference="P")
        deliver(order)
        item = order.items.first()
        rr = OrderService(order).request_return([{"order_item_id": str(item.pk), "quantity": 1}])
        s = client_for(staff)
        assert client_for(user).post(f"/api/returns/{rr.pk}/approve/").status_code == 403
        assert s.post(f"/api/returns/{rr.pk}/approve/", {"note": "ok"}).json()["status"] == "approved"
        data = s.post(f"/api/returns/{rr.pk}/receive/").json()
        assert data["status"] == "received" and data["refund"]["amount"] == "10000.00"
        rr2 = OrderService(order).request_return([{"order_item_id": str(item.pk), "quantity": 1}])
        assert s.post(f"/api/returns/{rr2.pk}/reject/").json()["status"] == "rejected"
        assert s.get("/api/returns/?status=rejected").json()["count"] == 1


class TestInfra:
    def test_export_command(self, order, tmp_path):
        order.email = "=cmd|' /C calc'!A0"
        order.save()
        path = tmp_path / "orders.csv"
        out = StringIO()
        call_command(
            "export_orders",
            f"--output={path}",
            "--status=pending",
            f"--from-date={timezone.now().date()}",
            f"--to-date={timezone.now().date()}",
            stdout=out,
        )
        content = path.read_text(encoding="utf-8")
        assert "Exported 1 orders" in out.getvalue() and order.order_number in content
        assert "'=cmd" in content  # formula injection neutralised

    def test_migrations_and_admin(self, client, order, staff):
        from django.contrib.auth import get_user_model

        call_command("makemigrations", "flexcommerce_orders", "--check", "--dry-run", stdout=StringIO())
        admin = get_user_model().objects.create_superuser("root", "r@x.com", "x")
        client.force_login(admin)
        for m in ("order", "shipment", "refund", "returnrequest"):
            assert client.get(f"/admin/flexcommerce_orders/{m}/").status_code == 200
        assert client.get(f"/admin/flexcommerce_orders/order/{order.pk}/change/").status_code == 200
        client.post(
            "/admin/flexcommerce_orders/order/",
            {"action": "confirm_orders", "_selected_action": [str(order.pk)]},
        )
        order.refresh_from_db()
        assert order.status == Order.STATUS_CONFIRMED
        client.post(
            "/admin/flexcommerce_orders/order/",
            {"action": "mark_processing", "_selected_action": [str(order.pk)]},
        )
        resp = client.post(
            "/admin/flexcommerce_orders/order/",
            {"action": "mark_delivered", "_selected_action": [str(order.pk)]},
            follow=True,
        )
        assert resp.status_code == 200
        client.post(
            "/admin/flexcommerce_orders/order/",
            {"action": "cancel_orders", "_selected_action": [str(order.pk)]},
        )
        OrderService(order).mark_paid(reference="late")
        refund = order.refunds.first()
        client.post(
            "/admin/flexcommerce_orders/refund/",
            {"action": "process_refunds", "_selected_action": [str(refund.pk)]},
        )
        refund.refresh_from_db()
        assert refund.status == Refund.STATUS_PROCESSED
        client.post(
            "/admin/flexcommerce_orders/refund/",
            {"action": "process_refunds", "_selected_action": [str(refund.pk)]},
        )
        assert str(order.items.first()).endswith("× 2") and "Refund(" in str(refund)
