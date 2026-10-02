"""
End-to-end journeys with every FlexCommerce app installed, a custom user model,
Paystack (mocked HTTP), outgoing webhooks and notifications.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from flexcommerce_core.jobs import run_due_jobs

from .conftest import ADDRESS, api

pytestmark = pytest.mark.django_db
D = Decimal


def emails_to(address):
    return [m.subject for m in mail.outbox if address in m.to]


def test_marketplace_purchase_to_refund_and_payout(
    customer, staff, seller, shipping, catalog, vendor_product, paystack, webhook_sink
):
    from flexcommerce_analytics import reports
    from flexcommerce_catalog.models import Product
    from flexcommerce_discounts.models import Coupon
    from flexcommerce_engagement.models import ProductReview
    from flexcommerce_inventory.models import InventoryItem
    from flexcommerce_marketplace.models import Payout, VendorOrder
    from flexcommerce_orders.models import Order
    from flexcommerce_payments.models import Payment

    shopper, admin_api, seller_api = api(customer), api(staff), api(seller)
    black, loafer = catalog["black"], vendor_product["variant"]

    # Browse: listing, filters, detail with live stock, recently viewed.
    listing = api().get("/api/catalog/products/?category=phones&brand=tecno").json()
    assert [p["slug"] for p in listing["results"]] == ["tecno-camon-30"]
    detail = api().get("/api/catalog/products/tecno-camon-30/").json()
    assert {v["sku"]: v["in_stock"] for v in detail["variants"]} == {"CAMON30-BLK": True, "CAMON30-WHT": True}
    shopper.post("/api/recently-viewed/track/", {"product_id": str(catalog["phone"].pk)}, format="json")

    # Wishlist the vendor product, then move it to the cart (catalog Product → default variant).
    wishlist = shopper.get("/api/wishlists/").json()[0]
    item = shopper.post(
        f"/api/wishlists/{wishlist['id']}/add/", {"product_id": str(vendor_product["product"].pk)}, format="json"
    ).json()
    assert "id" in item, item
    assert shopper.post(f"/api/wishlists/{wishlist['id']}/items/{item['id']}/move-to-cart/").json()["moved"] is True

    # Add the phone; apply a 10% coupon capped at ₦20,000.
    cart = shopper.post("/api/cart/add/", {"product_id": str(black.pk), "quantity": 1}, format="json").json()
    assert cart["item_count"] == 2 and cart["subtotal"] == "280000.00"
    phone_line = next(i for i in cart["items"] if i["product_id"] == str(black.pk))
    assert phone_line["product"]["image"] == "https://cdn.naijamart.example/camon.jpg"
    Coupon.objects.create(code="NAIJA10", name="10% off", value=D("10"), max_discount=D("20000"))
    cart = shopper.post("/api/cart/coupon/", {"code": "naija10"}, format="json").json()
    assert cart["discount_amount"] == "20000.00" and cart["total"] == "260000.00"

    # Delivery options and a live preview.
    options = shopper.post("/api/checkout/shipping-options/", {"state": "Lagos"}, format="json").json()
    assert [o["name"] for o in options["options"]] == ["Pickup station", "Door delivery (Lagos)"]
    door = shipping["door"]
    preview = shopper.post(
        "/api/checkout/preview/", {"shipping_address": ADDRESS, "shipping_method_id": str(door.pk)}, format="json"
    ).json()
    assert preview["grand_total"] == "261500.00"

    # Checkout with Paystack.
    resp = shopper.post(
        "/api/checkout/",
        {
            "payment_method": "paystack",
            "shipping_address": ADDRESS,
            "shipping_method_id": str(door.pk),
            "idempotency_key": "journey-1",
            "expected_total": "261500.00",
            "callback_url": "https://naijamart.example/checkout/complete",
        },
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["requires_redirect"] and body["redirect_url"] == "https://checkout.paystack.com/xyz"
    order = Order.objects.get()
    assert (order.status, order.payment_status, order.grand_total) == ("pending", "pending", D("261500.00"))
    assert order.discount_amount == D("20000.00") and order.shipping_cost == D("1500.00")
    assert order.payment_due_at is not None
    phone_stock = InventoryItem.objects.get(object_id=black.pk)
    assert (phone_stock.on_hand, phone_stock.reserved) == (10, 1)  # reserved, not yet sold
    vendor_order = VendorOrder.objects.get(order=order)
    assert vendor_order.gross_amount == D("27857.14")  # vendor's share after the proportional discount
    assert shopper.get("/api/cart/").json()["item_count"] == 0  # cart closed

    # Paystack confirms: verify endpoint (redirect) and webhook both land safely.
    paystack.state["verify_amount"] = 26150000
    reference = body["payment"]["reference"]
    assert api().get(f"/api/payments/verify/?reference={reference}").json()["status"] == "success"
    order.refresh_from_db()
    assert (order.status, order.payment_status, order.amount_paid) == ("confirmed", "paid", D("261500.00"))
    phone_stock.refresh_from_db()
    assert (phone_stock.on_hand, phone_stock.reserved, phone_stock.sold) == (9, 0, 1)
    assert Payment.objects.get(reference=reference).channel == "card"
    assert Coupon.objects.get(code="NAIJA10").used_count == 1
    vendor_order.refresh_from_db()
    assert vendor_order.status == VendorOrder.STATUS_CONFIRMED
    assert Product.objects.get(pk=catalog["phone"].pk).sold_count == 1

    # Notifications: customer, staff and vendor.
    assert emails_to("adaeze@example.com") == [
        f"Order {order.order_number} received",
        f"Payment received for order {order.order_number}",
    ]
    assert f"New order {order.order_number} — NGN 261,500.00" in emails_to("ops@naijamart.example")
    assert f"New order {order.order_number}" in emails_to("seller@abashoes.example")
    # Outgoing webhooks, signed.
    events = [json.loads(call.args[1])["event"] for call in webhook_sink.call_args_list]
    assert {"order.created", "order.paid", "order.confirmed", "payment.success"} <= set(events)

    # Fulfilment: the vendor ships their own item, staff ship the rest and confirm delivery.
    vo_id = seller_api.get("/api/vendors/me/orders/").json()["results"][0]["id"]
    assert (
        seller_api.post(
            f"/api/vendors/me/orders/{vo_id}/ship/", {"carrier": "Kwik", "tracking_number": "KW1"}, format="json"
        ).status_code
        == 201
    )
    order.refresh_from_db()
    assert order.status == "partially_shipped"
    admin_api.post(f"/api/orders/{order.pk}/shipments/", {"carrier": "GIG", "tracking_number": "GIG9"}, format="json")
    admin_api.post(f"/api/orders/{order.pk}/deliver/")
    order.refresh_from_db()
    assert order.status == "delivered"
    assert f"Order {order.order_number} delivered" in emails_to("adaeze@example.com")
    vendor_order.refresh_from_db()
    assert vendor_order.status == VendorOrder.STATUS_DELIVERED

    # Verified review → approved → product and vendor ratings update.
    review = shopper.post(
        "/api/reviews/",
        {
            "product_type": "flexcommerce_catalog.product",
            "product_id": str(vendor_product["product"].pk),
            "rating": 4,
            "body": "Very comfortable",
        },
        format="json",
    ).json()
    assert review["is_verified_purchase"] is True
    admin_api.post(f"/api/reviews/{review['id']}/approve/")
    assert Product.objects.get(pk=vendor_product["product"].pk).rating_avg == D("4.00")
    vendor_product["vendor"].refresh_from_db()
    assert vendor_product["vendor"].rating_avg == D("4.00")
    assert ProductReview.objects.get().is_approved

    # Return the loafers → approve, receive (restock), refund through Paystack.
    loafer_line = order.items.get(object_id=loafer.pk)
    rr = shopper.post(
        f"/api/orders/{order.pk}/return/",
        {"items": [{"order_item_id": str(loafer_line.pk), "quantity": 1}], "reason_code": "damaged"},
        format="json",
    ).json()
    admin_api.post(f"/api/returns/{rr['id']}/approve/")
    received = admin_api.post(f"/api/returns/{rr['id']}/receive/").json()
    assert received["refund"]["amount"] == "27857.14"
    loafer_stock = InventoryItem.objects.get(object_id=loafer.pk)
    assert loafer_stock.on_hand == 5  # 5 - 1 sold + 1 returned
    refund = admin_api.post(f"/api/orders/{order.pk}/refunds/{received['refund']['id']}/process/").json()
    assert refund["status"] == "processed" and refund["reference"] == "9001"
    order.refresh_from_db()
    assert (order.status, order.payment_status) == ("partially_refunded", "partially_refunded")
    assert f"Refund for order {order.order_number}" in emails_to("adaeze@example.com")
    vendor_order.refresh_from_db()
    assert vendor_order.refunded_amount == D("27857.14") and vendor_order.net_amount == D("0.00")

    # Analytics reflects sale and refund.
    today = timezone.localdate()
    revenue = reports.revenue_report(today, today)[0]
    assert revenue["gross"] == "261500.00" and revenue["refunds"] == "27857.14"
    assert reports.top_products_report(today, today)[0]["sku"] == "CAMON30-BLK"

    # Payouts after the return window: nothing left for the vendor (fully refunded).
    VendorOrder.objects.update(available_at=timezone.now() - timedelta(minutes=1))
    assert run_due_jobs(force=True, only=["marketplace.payouts"])["marketplace.payouts"] == {"payouts": 0}
    assert not Payout.objects.exists()


def test_guest_pay_on_delivery_and_tracking(shipping, catalog):
    from flexcommerce_inventory.models import InventoryItem
    from flexcommerce_orders.models import Order

    guest = api()
    token = guest.post("/api/cart/add/", {"product_id": str(catalog["white"].pk), "quantity": 2}, format="json").json()[
        "cart_token"
    ]
    mobile = api(HTTP_X_CART_TOKEN=token)  # same cart from a cookie-less mobile client
    over_limit = mobile.post(
        "/api/checkout/",
        {
            "payment_method": "pay_on_delivery",
            "shipping_address": ADDRESS,
            "email": "guest@example.com",
            "shipping_method_id": str(shipping["pickup"].pk),
            "pickup_station_id": str(shipping["station"].pk),
        },
        format="json",
    )
    assert over_limit.json()["error"] == "pod_limit_exceeded"  # ₦510,800 > ₦300,000 POD limit
    line = mobile.get("/api/cart/").json()["items"][0]
    mobile.patch(f"/api/cart/{line['id']}/update/", {"quantity": 1}, format="json")
    resp = mobile.post(
        "/api/checkout/",
        {
            "payment_method": "pay_on_delivery",
            "shipping_address": ADDRESS,
            "email": "guest@example.com",
            "shipping_method_id": str(shipping["pickup"].pk),
            "pickup_station_id": str(shipping["station"].pk),
        },
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    order = Order.objects.get()
    assert order.status == "confirmed" and order.pickup_station["code"] == "lekki" and order.shipping_cost == D("800")
    stock = InventoryItem.objects.get(object_id=catalog["white"].pk)
    assert (stock.on_hand, stock.reserved) == (9, 0)  # committed immediately for pay-on-delivery
    assert emails_to("guest@example.com") == [f"Order {order.order_number} received"]
    tracked = (
        api().post("/api/orders/track/", {"order_number": order.order_number, "email": "guest@example.com"}).json()
    )
    assert tracked["status"] == "confirmed"
    link = api().get(f"/api/orders/guest/{order.order_number}/?token={resp.json()['access_token']}")
    assert link.status_code == 200


def test_unpaid_order_expires_and_releases_everything(customer, shipping, catalog, paystack):
    from flexcommerce_discounts.models import Coupon, FlashSale, FlashSaleItem
    from flexcommerce_inventory.models import InventoryItem
    from flexcommerce_orders.models import Order

    black = catalog["black"]
    now = timezone.now()
    sale = FlashSale.objects.create(
        name="Midnight", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1)
    )
    from django.contrib.contenttypes.models import ContentType

    FlashSaleItem.objects.create(
        sale=sale,
        content_type=ContentType.objects.get_for_model(black),
        object_id=black.pk,
        sale_price=D("199000"),
        quantity_limit=5,
    )
    Coupon.objects.create(code="ONCE", name="x", coupon_type=Coupon.TYPE_FIXED, value=D("1000"), usage_limit=1)
    shopper = api(customer)
    cart = shopper.post("/api/cart/add/", {"product_id": str(black.pk), "quantity": 2}, format="json").json()
    assert cart["subtotal"] == "398000.00"  # flash price applied
    shopper.post("/api/cart/coupon/", {"code": "ONCE"}, format="json")
    resp = shopper.post(
        "/api/checkout/",
        {"payment_method": "card", "shipping_address": ADDRESS, "shipping_method_id": str(shipping["door"].pk)},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    order = Order.objects.get()
    assert FlashSaleItem.objects.get().sold_quantity == 2 and Coupon.objects.get().used_count == 1
    # The customer never pays: the deadline passes and the maintenance jobs clean up.
    Order.objects.update(payment_due_at=timezone.now() - timedelta(minutes=1))
    results = run_due_jobs(force=True, only=["orders.expire_unpaid"])
    assert results["orders.expire_unpaid"] == {"cancelled": 1}
    order.refresh_from_db()
    assert order.status == "cancelled"
    stock = InventoryItem.objects.get(object_id=black.pk)
    assert (stock.on_hand, stock.reserved) == (10, 0)
    assert FlashSaleItem.objects.get().sold_quantity == 0 and Coupon.objects.get().used_count == 0
    assert f"Order {order.order_number} cancelled" in emails_to("adaeze@example.com")


def test_wallet_refund_then_wallet_checkout(customer, staff, shipping, catalog):
    from flexcommerce_orders.models import Order
    from flexcommerce_payments.models import Wallet

    shopper = api(customer)
    shopper.post("/api/cart/add/", {"product_id": str(catalog["black"].pk)}, format="json")
    first = shopper.post(
        "/api/checkout/",
        {
            "payment_method": "pay_on_delivery",
            "shipping_address": ADDRESS,
            "shipping_method_id": str(shipping["door"].pk),
        },
        format="json",
    ).json()
    order = Order.objects.get(pk=first["order"]["id"])
    admin = api(staff)
    admin.post(f"/api/orders/{order.pk}/mark-paid/", {"reference": "CASH-1"}, format="json")
    refund = admin.post(
        f"/api/orders/{order.pk}/refund/",
        {"amount": "251500", "reason": "Cancelled after delivery", "method": "wallet", "process": True},
        format="json",
    ).json()
    assert refund["status"] == "processed"
    assert shopper.get("/api/wallet/").json()["balance"] == "251500.00"
    assert "NGN 251,500.00 added to your wallet" in emails_to("adaeze@example.com")

    shopper.post("/api/cart/add/", {"product_id": str(catalog["white"].pk)}, format="json")
    short = shopper.post(
        "/api/checkout/",
        {"payment_method": "wallet", "shipping_address": ADDRESS, "shipping_method_id": str(shipping["door"].pk)},
        format="json",
    )
    assert short.status_code == 402 and short.json()["error"] == "payment_method_unavailable"  # 256,500 > balance
    assert shopper.get("/api/cart/").json()["item_count"] == 1  # nothing lost
    admin.post(
        "/api/wallet/adjust/", {"user_id": str(customer.pk), "amount": "5000", "description": "Goodwill"}, format="json"
    )
    assert "wallet" in [m["key"] for m in shopper.get("/api/checkout/payment-methods/").json()]
    second = shopper.post(
        "/api/checkout/",
        {"payment_method": "wallet", "shipping_address": ADDRESS, "shipping_method_id": str(shipping["door"].pk)},
        format="json",
    )
    assert second.status_code == 201, second.json()
    paid = Order.objects.get(pk=second.json()["order"]["id"])
    assert (paid.status, paid.payment_status) == ("confirmed", "paid")
    assert Wallet.objects.get(user=customer).balance == D("0.00")


def test_back_in_stock_and_abandoned_cart_messages(customer, catalog):
    from flexcommerce_cart.models import Cart
    from flexcommerce_inventory.models import InventoryItem

    white = catalog["white"]
    item = InventoryItem.objects.get(object_id=white.pk)
    item.adjust(0)
    api().post("/api/inventory/alerts/", {"product_id": str(white.pk), "email": "waiting@example.com"}, format="json")
    item.restock(3)
    assert emails_to("waiting@example.com") == ["Tecno Camon 30 is back in stock"]

    shopper = api(customer)
    shopper.post("/api/cart/add/", {"product_id": str(catalog["black"].pk)}, format="json")
    Cart.objects.update(last_activity=timezone.now() - timedelta(hours=3))
    run_due_jobs(force=True, only=["cart.abandoned"])
    assert "You left something in your cart" in emails_to("adaeze@example.com")
