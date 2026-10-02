from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from flexcommerce_core.exceptions import FlexCommerceError, VendorError, VendorNotApprovedError
from flexcommerce_marketplace import services
from flexcommerce_marketplace.models import Payout, Vendor, VendorMember, VendorOrder
from flexcommerce_orders.models import Order, Shipment
from flexcommerce_orders.services import OrderService

from .conftest import client_for, order_with

pytestmark = pytest.mark.django_db
D = Decimal


def deliver_everything(order):
    service = OrderService(order)
    service.mark_paid(reference="P")
    order.refresh_from_db()
    shipment = service.create_shipment(carrier="GIG")
    service.update_shipment(shipment, Shipment.STATUS_DELIVERED)
    order.refresh_from_db()
    return order


class TestOnboarding:
    def test_apply_and_approve_flow(self, seller, staff):
        c = client_for(seller)
        resp = c.post(
            "/api/vendors/apply/",
            {
                "name": "Lekki Gadgets",
                "account_number": "0123456789",
                "bank_name": "GTBank",
                "business_registration_number": "RC123",
            },
            format="json",
        )
        assert resp.status_code == 201 and resp.json()["status"] == "pending"
        slug = resp.json()["slug"]
        assert c.post("/api/vendors/apply/", {"name": "Again"}, format="json").json()["error"] == "already_vendor"
        assert c.get("/api/vendors/me/").json()["name"] == "Lekki Gadgets"
        assert c.get("/api/vendors/me/orders/").json()["error"] == "vendor_not_approved"
        assert client_for().get("/api/vendors/").json()["count"] == 0  # pending shops are hidden
        s = client_for(staff)
        assert s.get("/api/vendors/?status=pending").json()["count"] == 1
        assert c.post(f"/api/vendors/{slug}/approve/").status_code == 403
        assert s.post(f"/api/vendors/{slug}/approve/").json()["status"] == "approved"
        assert client_for().get(f"/api/vendors/{slug}/").json()["name"] == "Lekki Gadgets"
        assert (
            s.patch(
                f"/api/vendors/{slug}/", {"commission_rate": "0.0750", "is_official_store": True}, format="json"
            ).json()["commission_rate"]
            == "0.0750"
        )
        assert s.post(f"/api/vendors/{slug}/suspend/", {"reason": "fraud"}).json()["status_reason"] == "fraud"
        assert s.post(f"/api/vendors/{slug}/reject/").json()["status"] == "rejected"

    def test_profile_update_and_validation(self, vendor, seller):
        c = client_for(seller)
        assert c.patch("/api/vendors/me/", {"account_number": "123"}, format="json").status_code == 400
        resp = c.patch(
            "/api/vendors/me/", {"description": "Pots & pans", "status": "approved", "name": "Hacked"}, format="json"
        ).json()
        assert resp["description"] == "Pots & pans" and resp["name"] == "Mama Put Kitchenware"
        assert client_for().post("/api/vendors/apply/", {"name": "x"}).status_code in (401, 403)

    def test_non_vendor_and_auto_approve(self, buyer, fc):
        assert client_for(buyer).get("/api/vendors/me/").json()["error"] == "not_a_vendor"
        fc(PRODUCT_MODELS=["tests.Product"], VENDOR_AUTO_APPROVE=True)
        vendor = services.apply(buyer, name="Instant Shop")
        assert vendor.is_approved and vendor.approved_at and vendor.slug == "instant-shop"
        assert VendorMember.objects.get(user=buyer).role == VendorMember.ROLE_OWNER

    def test_catalog_hook(self, vendor, seller, buyer, fc):
        assert services.vendor_id_for_user(seller) == vendor.pk
        assert services.vendor_id_for_user(buyer) is None
        assert services.vendor_id_for_user(None) is None
        services.set_status(vendor, Vendor.STATUS_SUSPENDED)
        assert services.vendor_id_for_user(seller) is None  # suspended sellers lose product access
        with pytest.raises(VendorNotApprovedError):
            services.require_vendor(seller)

    def test_slug_uniqueness_and_commission(self, seller, seller2, fc):
        a = Vendor.objects.create(owner=seller, name="Shop")
        b = Vendor(owner=seller2, name="Shop!")
        b.save()
        assert a.slug == "shop" and b.slug == "shop-2"
        assert a.effective_commission_rate == D("0.1")
        assert str(a) == "Shop"


class TestSplitting:
    def test_order_split_per_vendor_with_commission(self, buyer, vendor, vendor2):
        order = order_with(buyer, (vendor, "10000", 2), (vendor2, "5000", 1), (None, "1000", 1))
        splits = {vo.vendor_id: vo for vo in VendorOrder.objects.filter(order=order)}
        assert set(splits) == {vendor.pk, vendor2.pk}
        a, b = splits[vendor.pk], splits[vendor2.pk]
        assert (a.gross_amount, a.commission_rate, a.commission_amount) == (D("20000.00"), D("0.1"), D("2000.00"))
        assert (b.commission_amount, b.net_amount) == (D("250.00"), D("4750.00"))
        assert order.items.get(vendor_id=vendor.pk).commission_rate == D("0.1")
        assert a.status == VendorOrder.STATUS_PENDING and str(a).startswith(order.order_number)

    def test_lifecycle_sync(self, buyer, vendor, vendor2):
        order = order_with(buyer, (vendor, "10000", 1), (vendor2, "5000", 1))
        OrderService(order).mark_paid(reference="P")
        assert set(VendorOrder.objects.values_list("status", flat=True)) == {VendorOrder.STATUS_CONFIRMED}
        # vendor 1 ships its own items only
        vo = VendorOrder.objects.get(vendor=vendor)
        services.ship_vendor_order(vo, carrier="Kwik")
        vo.refresh_from_db()
        assert vo.status == VendorOrder.STATUS_SHIPPED
        assert VendorOrder.objects.get(vendor=vendor2).status == VendorOrder.STATUS_CONFIRMED
        order.refresh_from_db()
        assert order.status == Order.STATUS_PARTIALLY_SHIPPED
        with pytest.raises(VendorError):
            services.ship_vendor_order(vo)

    def test_cancel_marks_vendor_orders(self, buyer, vendor):
        order = order_with(buyer, (vendor, "10000", 1))
        OrderService(order).cancel()
        assert VendorOrder.objects.get().status == VendorOrder.STATUS_CANCELLED

    def test_order_without_vendors(self, buyer):
        order_with(buyer, (None, "1000", 1))
        assert VendorOrder.objects.count() == 0


class TestVendorAPI:
    def test_orders_ship_and_summary(self, buyer, vendor, vendor2, seller):
        order = order_with(buyer, (vendor, "10000", 1), (vendor2, "5000", 1))
        OrderService(order).mark_paid(reference="P")
        c = client_for(seller)
        listing = c.get("/api/vendors/me/orders/").json()
        assert listing["count"] == 1
        vo = listing["results"][0]
        assert [i["unit_price"] for i in vo["items"]] == ["10000.00"]  # never sees the other vendor's items
        assert vo["shipping_address"]["city"] == "Ikeja"
        assert c.get("/api/vendors/me/orders/?status=shipped").json()["count"] == 0
        shipped = c.post(f"/api/vendors/me/orders/{vo['id']}/ship/", {"carrier": "GIG", "tracking_number": "G1"})
        assert shipped.status_code == 201 and shipped.json()["status"] == "shipped"
        other = VendorOrder.objects.get(vendor=vendor2)
        assert c.post(f"/api/vendors/me/orders/{other.pk}/ship/", {}).status_code == 404
        summary = c.get("/api/vendors/me/summary/").json()
        assert summary["gross_sales"] == "10000.00" and summary["commission"] == "1000.00"
        assert summary["orders_by_status"]["shipped"] == 1 and summary["available_for_payout"] == "0.00"


class TestPayouts:
    def test_hold_period_refunds_and_payout(self, buyer, vendor, vendor2, staff, seller):
        order = order_with(buyer, (vendor, "10000", 2), (vendor2, "5000", 1))
        deliver_everything(order)
        vo = VendorOrder.objects.get(vendor=vendor)
        assert vo.status == VendorOrder.STATUS_DELIVERED and vo.available_at > timezone.now() + timedelta(days=6)
        assert services.generate_payouts() == {"payouts": 0}  # still inside the return window

        # a returned unit is charged back to the right vendor
        item = order.items.get(vendor_id=vendor.pk)
        service = OrderService(order)
        refund = service.create_refund(D("10000"), "returned", items=[{"order_item_id": str(item.pk), "quantity": 1}])
        service.process_refund(refund)
        vo.refresh_from_db()
        assert vo.refunded_amount == D("10000.00") and vo.net_amount == D("9000.00")
        assert VendorOrder.objects.get(vendor=vendor2).refunded_amount == D("0.00")

        VendorOrder.objects.update(available_at=timezone.now() - timedelta(minutes=1))
        s = client_for(staff)
        assert s.post("/api/payouts/generate/").json() == {"payouts": 2}
        assert s.post("/api/payouts/generate/").json() == {"payouts": 0}
        payout = Payout.objects.get(vendor=vendor)
        assert payout.amount == D("9000.00") and payout.bank_details["account_name"] == ""
        mine = client_for(seller).get("/api/vendors/me/payouts/").json()
        assert mine["count"] == 1 and mine["results"][0]["order_count"] == 1
        failed = s.post(f"/api/payouts/{payout.pk}/mark-failed/", {"reason": "wrong account"}).json()
        assert failed["status"] == "failed"
        vo.refresh_from_db()
        assert vo.payout is None  # payable again
        payout2 = services.create_payout(vendor)
        paid = s.post(f"/api/payouts/{payout2.pk}/mark-paid/", {"reference": "TRF-1"}).json()
        assert paid["status"] == "paid" and paid["reference"] == "TRF-1"
        assert s.post(f"/api/payouts/{payout2.pk}/mark-paid/").status_code == 400
        assert s.get("/api/payouts/?status=paid").json()["count"] == 1
        assert client_for(seller).get("/api/payouts/").status_code == 403
        summary = services.vendor_summary(vendor)
        assert summary["paid_out"] == D("9000.00")

    def test_proportional_refund_allocation(self, buyer, vendor, vendor2):
        order = order_with(buyer, (vendor, "10000", 1), (vendor2, "10000", 1))
        OrderService(order).mark_paid(reference="P")
        service = OrderService(order)
        service.process_refund(service.create_refund(D("2000"), "goodwill"))
        assert sorted(VendorOrder.objects.values_list("refunded_amount", flat=True)) == [D("1000.00"), D("1000.00")]

    def test_minimum_payout(self, buyer, vendor, fc):
        fc(PRODUCT_MODELS=["tests.Product"], VENDOR_MINIMUM_PAYOUT=100000, RETURN_WINDOW_DAYS=0)
        order = order_with(buyer, (vendor, "1000", 1))
        deliver_everything(order)
        assert services.create_payout(vendor) is None

    def test_hold_days_setting(self, fc):
        fc(PRODUCT_MODELS=["tests.Product"], VENDOR_PAYOUT_HOLD_DAYS=14)
        assert services.hold_days() == 14

    def test_command(self):
        out = StringIO()
        call_command("generate_payouts", stdout=out)
        assert "Created 0 payouts" in out.getvalue()


class TestInfra:
    def test_migrations_and_admin(self, client, buyer, vendor):
        from django.contrib.auth import get_user_model

        call_command("makemigrations", "flexcommerce_marketplace", "--check", "--dry-run", stdout=StringIO())
        order = order_with(buyer, (vendor, "10000", 1))
        client.force_login(get_user_model().objects.create_superuser("root", "r@x.com", "x"))
        for m in ("vendor", "vendororder", "payout"):
            assert client.get(f"/admin/flexcommerce_marketplace/{m}/").status_code == 200
        client.post(
            "/admin/flexcommerce_marketplace/vendor/", {"action": "suspend", "_selected_action": [str(vendor.pk)]}
        )
        vendor.refresh_from_db()
        assert vendor.status == Vendor.STATUS_SUSPENDED
        client.post(
            "/admin/flexcommerce_marketplace/vendor/", {"action": "approve", "_selected_action": [str(vendor.pk)]}
        )
        vendor.refresh_from_db()
        assert vendor.is_approved and "Payout(" in str(Payout(vendor=vendor, amount=1))
        assert str(VendorMember.objects.first()).endswith("(owner)") and order

    def test_mark_payout_guard(self, vendor):
        payout = Payout.objects.create(vendor=vendor, amount=D("1"), status=Payout.STATUS_PAID)
        with pytest.raises(FlexCommerceError):
            services.mark_payout(payout, True)
