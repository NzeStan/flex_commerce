import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from flexcommerce_analytics import reports
from flexcommerce_analytics.aggregation import aggregate_range, aggregate_yesterday
from flexcommerce_analytics.models import AnalyticsEvent, DailyRevenueSummary
from flexcommerce_cart.models import Cart
from flexcommerce_discounts.models import Coupon
from flexcommerce_discounts.services import redeem
from flexcommerce_orders.models import Order
from flexcommerce_orders.services import OrderService

from .conftest import sale

pytestmark = pytest.mark.django_db
D = Decimal


def today():
    return timezone.localdate()


class TestReports:
    def test_revenue_counts_confirmed_sales_only(self):
        sale(price="10750")
        sale(price="21500", qty=1)
        sale(price="5000", confirm=False)  # pending: not a sale
        cancelled = sale(price="9999")
        OrderService(cancelled).cancel()
        rows = reports.revenue_report(today(), today())
        assert len(rows) == 1
        row = rows[0]
        assert row["order_count"] == 2 and row["gross"] == "32250.00" and row["tax"] == "2250.00"
        assert row["net"] == "30000.00" and row["collected"] == "32250.00" and row["average_order_value"] == "16125.00"
        assert row["refunds"] == "0.00" and row["currency"] == "NGN"
        assert len(reports.revenue_report(today(), today(), period="monthly")) == 1

    def test_refunds_dated_by_processing(self):
        order = sale(price="10750")
        service = OrderService(order)
        service.process_refund(service.create_refund(D("1000"), "x"))
        Order.objects.filter(pk=order.pk).update(confirmed_at=timezone.now() - timedelta(days=40))
        rows = reports.revenue_report(today(), today())
        assert rows == [
            {
                "period": today().isoformat(),
                "currency": "NGN",
                "order_count": 0,
                "gross": "0.00",
                "net": "0.00",
                "tax": "0.00",
                "shipping": "0.00",
                "discounts": "0.00",
                "collected": "0.00",
                "refunds": "1000.00",
                "average_order_value": "0.00",
            }
        ]

    def test_top_products(self):
        from tests.models import Product

        rice = Product.objects.create(name="Rice", sku="RICE", price=D("5000"))
        beans = Product.objects.create(name="Beans", sku="BEANS", price=D("20000"))
        sale(product=rice, qty=5)
        sale(product=beans, qty=1)
        by_revenue = reports.top_products_report(today(), today(), by="revenue")
        assert [r["sku"] for r in by_revenue] == ["RICE", "BEANS"]  # 25000 vs 20000
        by_qty = reports.top_products_report(today(), today(), by="quantity", limit=1)
        assert by_qty[0]["quantity_sold"] == 5 and len(by_qty) == 1

    def test_abandoned_carts_and_conversion(self):
        now = timezone.now()
        Cart.objects.create(session_key="a", abandoned_notified_at=now, total_amount=D("5000"), items_count=1)
        Cart.objects.create(
            session_key="b",
            abandoned_notified_at=now,
            total_amount=D("15000"),
            items_count=2,
            status=Cart.STATUS_ORDERED,
        )
        Cart.objects.create(session_key="c", items_count=0)
        report = reports.abandoned_cart_report(today(), today())
        assert report["total_abandoned"] == 2 and report["total_value"] == "20000.00"
        assert report["recovered"] == 1 and report["recovery_rate"] == 50.0 and report["average_value"] == "10000.00"
        assert report["by_day"][0]["count"] == 2
        conversion = reports.conversion_report(today(), today())
        assert conversion == {"carts_with_items": 2, "orders": 1, "conversion_rate": 50.0}

    def test_coupons(self):
        Coupon.objects.create(code="NAIJA10", name="x", value=D("10"), per_user_limit=0)
        order = sale(price="10750", coupon="NAIJA10", discount=D("1075"))
        redeem("NAIJA10", order_ref=order.order_number, amount=D("1075"), email="a@example.com")
        rows = reports.coupon_performance_report(today(), today())
        assert rows == [
            {
                "code": "NAIJA10",
                "usage_count": 1,
                "total_discount": "1075.00",
                "orders_revenue": "9675.00",
            }
        ]

    def test_vendors_state_payment_customers(self):
        vendor = uuid.uuid4()
        sale(price="10000", vendor_id=vendor, state="lagos", method="card", email="x@example.com")
        sale(price="20000", state="Rivers", method="pay_on_delivery", email="x@example.com")
        sale(price="5000", state="Rivers", method="pay_on_delivery", email="new@example.com")
        vendors = reports.vendor_performance_report(today(), today())
        assert vendors == [
            {
                "vendor_id": str(vendor),
                "items_sold": 1,
                "gross_revenue": "10000.00",
                "commission": "0.00",
                "order_count": 1,
            }
        ]
        states = reports.sales_by_state_report(today(), today())
        assert [(s["state"], s["order_count"]) for s in states] == [("Rivers", 2), ("Lagos", 1)]
        methods = {m["payment_method"]: m["order_count"] for m in reports.payment_methods_report(today(), today())}
        assert methods == {"card": 1, "pay_on_delivery": 2}
        customers = reports.customer_report(today(), today())
        assert customers == {
            "customers": 2,
            "new_customers": 2,
            "returning_customers": 0,
            "repeat_purchase_rate": 0.0,
        }
        old = Order.objects.filter(email="x@example.com").first()
        Order.objects.filter(pk=old.pk).update(confirmed_at=timezone.now() - timedelta(days=90))
        customers = reports.customer_report(today(), today())
        assert customers["returning_customers"] == 1

    def test_dashboard(self):
        sale(price="10750")
        pending = sale(price="5000", confirm=False)
        OrderService(sale(price="100")).create_refund(D("50"), "x")
        summary = reports.dashboard_summary()
        assert summary["today"]["NGN"]["orders"] == 2
        assert summary["needs_attention"]["to_ship"] == 2
        assert summary["needs_attention"]["awaiting_payment"] == 1
        assert summary["needs_attention"]["refunds_to_process"] == 1 and pending


class TestEventsAndAggregation:
    def test_events_recorded_from_signals(self):
        order = sale(price="10750")
        OrderService(order).cancel()
        types = set(AnalyticsEvent.objects.values_list("event_type", flat=True))
        assert {"order.created", "order.paid", "order.confirmed", "order.cancelled"} <= types
        created = AnalyticsEvent.objects.get(event_type="order.created")
        assert created.reference == order.order_number and created.amount == D("10750.00")
        assert str(created) == f"AnalyticsEvent(order.created, ref={order.order_number})"

    def test_record_errors_are_swallowed(self, monkeypatch):
        from flexcommerce_analytics import signal_handlers

        monkeypatch.setattr(AnalyticsEvent.objects, "create", lambda **kw: 1 / 0)
        signal_handlers._record("order.created")  # must not raise

    def test_daily_aggregation(self):
        sale(price="10750", qty=2, email="n@example.com")
        refund_order = sale(price="1000")
        service = OrderService(refund_order)
        service.process_refund(service.create_refund(D("500"), "x"))
        cancelled = sale(price="10", confirm=False)
        OrderService(cancelled).cancel()
        Cart.objects.create(session_key="z", abandoned_notified_at=timezone.now(), items_count=1)
        assert aggregate_range(today(), today()) == {"created": 1, "updated": 0}
        row = DailyRevenueSummary.objects.get(date=today(), currency="NGN")
        assert (row.order_count, row.units_sold, row.gross_revenue) == (2, 3, D("22500.00"))
        assert (row.refund_total, row.cancelled_order_count, row.abandoned_cart_count) == (
            D("500.00"),
            1,
            1,
        )
        assert row.new_customers == 2 and row.average_order_value == D("11250.00")
        assert aggregate_range(today(), today()) == {"created": 0, "updated": 0}
        assert aggregate_range(today(), today(), rebuild=True) == {"created": 0, "updated": 1}
        assert aggregate_yesterday()["updated"] == 1
        assert str(row).startswith("Revenue(")


class TestAPI:
    def test_endpoints(self, staff_client, client):
        sale(price="10750")
        assert client.get("/api/analytics/revenue/").status_code in (401, 403)
        for path in (
            "revenue",
            "top-products",
            "abandoned-carts",
            "conversion",
            "coupon-performance",
            "vendor-performance",
            "sales-by-state",
            "payment-methods",
            "customers",
            "daily-summary",
            "dashboard",
        ):
            assert staff_client.get(f"/api/analytics/{path}/").status_code == 200, path
        data = staff_client.get("/api/analytics/revenue/?period=weekly").json()
        assert data["period"] == "weekly" and data["results"][0]["gross"] == "10750.00"

    @pytest.mark.parametrize(
        "query",
        [
            "start_date=bad",
            "start_date=2025-02-01&end_date=2025-01-01",
            "limit=0",
            "limit=abc",
            "period=hourly",
            "start_date=2000-01-01&end_date=2025-01-01",
            "report=nope",
        ],
    )
    def test_invalid_params_rejected(self, staff_client, query):
        path = "export" if query.startswith("report") else "top-products"
        assert staff_client.get(f"/api/analytics/{path}/?{query}").status_code == 400

    def test_csv_export(self, staff_client):
        from tests.models import Product

        empty = staff_client.get("/api/analytics/export/?report=revenue")
        assert empty.status_code == 200 and empty.json()["results"] == []
        sale(product=Product.objects.create(name="=HYPERLINK(evil)", sku="X", price=D("100")))
        resp = staff_client.get("/api/analytics/export/?report=top_products")
        body = b"".join(resp.streaming_content).decode()
        assert resp["Content-Type"].startswith("text/csv") and "'=HYPERLINK(evil)" in body
        revenue = b"".join(staff_client.get("/api/analytics/export/?report=revenue").streaming_content).decode()
        assert revenue.startswith("period,currency")
        states = b"".join(staff_client.get("/api/analytics/export/?report=sales_by_state").streaming_content).decode()
        assert "Lagos" in states


class TestCommands:
    def test_aggregate_command(self):
        sale(price="10750")
        out = StringIO()
        call_command("aggregate_analytics", f"--start-date={today()}", stdout=out)
        assert "Created: 1" in out.getvalue()
        call_command("aggregate_analytics", stdout=out)
        with pytest.raises(CommandError):
            call_command("aggregate_analytics", "--start-date=bad")
        with pytest.raises(CommandError):
            call_command("aggregate_analytics", "--start-date=2025-02-01", "--end-date=2025-01-01")

    def test_export_command(self, tmp_path):
        out = StringIO()
        call_command("export_analytics", "--report=revenue", stdout=out)
        assert "No data" in out.getvalue()
        sale(price="10750")
        path = tmp_path / "top.csv"
        call_command("export_analytics", "--report=top_products", f"--output={path}", stdout=out)
        assert "Exported 1 rows" in out.getvalue() and path.read_text(encoding="utf-8").startswith("product_id")
        call_command(
            "export_analytics",
            "--report=revenue",
            "--period=monthly",
            f"--output={tmp_path / 'r.csv'}",
            stdout=out,
        )
        call_command(
            "export_analytics",
            "--report=payment_methods",
            f"--output={tmp_path / 'p.csv'}",
            stdout=out,
        )
        with pytest.raises(CommandError):
            call_command("export_analytics", "--start-date=bad")

    def test_migrations_and_admin(self, client):
        from django.contrib.auth import get_user_model

        call_command("makemigrations", "flexcommerce_analytics", "--check", "--dry-run", stdout=StringIO())
        sale(price="100")
        aggregate_range(today(), today())
        client.force_login(get_user_model().objects.create_superuser("root", "r@x.com", "x"))
        for m in ("analyticsevent", "dailyrevenuesummary"):
            assert client.get(f"/admin/flexcommerce_analytics/{m}/").status_code == 200
