"""Every domain event reaches the right recipient through the notifications app."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from flexcommerce_notifications.models import NotificationLog

from .conftest import ADDRESS, api

pytestmark = pytest.mark.django_db


def logged(event, recipient=None, channel="email"):
    qs = NotificationLog.objects.filter(event=event, channel=channel)
    if recipient:
        qs = qs.filter(recipient=recipient)
    return qs.exists()


@pytest.fixture
def placed_order(customer, shipping, catalog):
    shopper = api(customer)
    shopper.post("/api/cart/add/", {"product_id": str(catalog["black"].pk)}, format="json")
    data = shopper.post(
        "/api/checkout/",
        {
            "payment_method": "bank_transfer",
            "shipping_address": ADDRESS,
            "shipping_method_id": str(shipping["door"].pk),
        },
        format="json",
    ).json()
    from flexcommerce_orders.models import Order

    return Order.objects.get(pk=data["order"]["id"])


def test_order_lifecycle_messages(placed_order, staff):
    from flexcommerce_orders.models import Shipment
    from flexcommerce_orders.services import OrderService

    order = placed_order
    email = "adaeze@example.com"
    assert logged("order.created", email) and logged("order.created", "2348031234567", "sms") is False
    assert logged("order.created", "08031234567", "sms")  # custom user model's phone field is used
    assert logged("staff.order_created", "ops@naijamart.example")
    service = OrderService(order)
    service.mark_payment_failed(reason="insufficient funds")
    assert logged("payment.failed", email)
    service.mark_paid(reference="BANK-1")
    shipment = service.create_shipment(carrier="GIG", tracking_number="G-1")
    assert logged("order.shipped", email)
    service.update_shipment(shipment, Shipment.STATUS_OUT_FOR_DELIVERY, location="Lekki")
    assert logged("shipment.out_for_delivery", email)
    service.update_shipment(shipment, Shipment.STATUS_DELIVERED)
    assert logged("order.delivered", email)
    item = order.items.first()
    rr = service.request_return([{"order_item_id": str(item.pk), "quantity": 1}], reason_code="damaged")
    assert logged("return.requested", "ops@naijamart.example")
    service.approve_return(rr)
    assert logged("return.updated", email)


def test_cancellation_and_in_app_inbox(placed_order, customer):
    from flexcommerce_orders.services import OrderService

    OrderService(placed_order).cancel(reason="Changed my mind", by_customer=True)
    assert logged("order.cancelled", "adaeze@example.com")
    inbox = api(customer).get("/api/notifications/inbox/").json()["results"]
    assert {n["event"] for n in inbox} == {"order.created", "order.cancelled"}


def test_engagement_marketplace_and_stock_messages(customer, staff, seller, catalog, vendor_product):
    from flexcommerce_engagement import services as engagement
    from flexcommerce_inventory.models import InventoryItem
    from flexcommerce_marketplace import services as marketplace
    from flexcommerce_marketplace.models import Payout

    # Vendor application / approval (vendor fixture already approved one shop).
    assert logged("vendor.applied", "ops@naijamart.example")
    assert logged("vendor.approved", "seller@abashoes.example")

    # Q&A: the seller's official answer notifies the asker.
    shopper, seller_api = api(customer), api(seller)
    question = shopper.post(
        "/api/questions/",
        {
            "product_type": "flexcommerce_catalog.product",
            "product_id": str(vendor_product["product"].pk),
            "question": "Is it real leather?",
        },
        format="json",
    ).json()
    api(staff).post(f"/api/questions/{question['id']}/approve/")
    answer = seller_api.post(f"/api/questions/{question['id']}/answers/", {"answer": "Yes, 100%."}).json()
    assert answer["is_official"] is True
    assert logged("question.answered", "adaeze@example.com")

    # Review awaiting moderation alerts staff.
    engagement.create_review(customer, catalog["phone"], 5, "Great phone")
    assert logged("review.submitted", "ops@naijamart.example")

    # Wishlist price drop.
    item, _ = engagement.add_to_wishlist(engagement.default_wishlist(customer), catalog["phone"])
    catalog["black"].price = Decimal("200000")
    catalog["black"].save()
    engagement.detect_price_drops()
    assert logged("wishlist.price_drop", "adaeze@example.com")

    # Low / out of stock alerts to staff.
    stock = InventoryItem.objects.get(object_id=catalog["white"].pk)
    stock.adjust(2)
    assert logged("inventory.low_stock", "ops@naijamart.example")
    stock.adjust(0)
    assert logged("inventory.out_of_stock", "ops@naijamart.example")

    # Payout status update to the vendor.
    payout = Payout.objects.create(vendor=vendor_product["vendor"], amount=Decimal("5000"))
    marketplace.mark_payout(payout, True, reference="TRF-9")
    assert logged("payout.updated", "seller@abashoes.example")


def test_preferences_are_respected_end_to_end(customer, shipping, catalog):
    from flexcommerce_notifications.models import NotificationPreference

    NotificationPreference.objects.create(user=customer, email_order_updates=False, sms_order_updates=False)
    shopper = api(customer)
    shopper.post("/api/cart/add/", {"product_id": str(catalog["black"].pk)}, format="json")
    shopper.post(
        "/api/checkout/",
        {
            "payment_method": "pay_on_delivery",
            "shipping_address": ADDRESS,
            "shipping_method_id": str(shipping["door"].pk),
        },
        format="json",
    )
    email_log = NotificationLog.objects.get(event="order.created", channel="email")
    assert email_log.status == NotificationLog.STATUS_SKIPPED
    assert NotificationLog.objects.get(event="order.created", channel="in_app").status == NotificationLog.STATUS_SENT


def test_abandoned_guest_cart_uses_contact_email(catalog):
    from flexcommerce_cart.models import Cart
    from flexcommerce_core.jobs import run_due_jobs

    guest = api()
    guest.post("/api/cart/add/", {"product_id": str(catalog["black"].pk)}, format="json")
    guest.post("/api/cart/contact/", {"email": "lead@example.com"}, format="json")
    Cart.objects.update(last_activity=timezone.now() - timedelta(hours=2))
    run_due_jobs(force=True, only=["cart.abandoned"])
    assert logged("cart.abandoned", "lead@example.com")
