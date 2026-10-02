import threading
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.utils import timezone

from flexcommerce_core.exceptions import InsufficientStockError, StockReservationError
from flexcommerce_inventory import services
from flexcommerce_inventory.models import InventoryItem, StockAlert, StockMovement, StockReservation
from tests.models import Product

pytestmark = pytest.mark.django_db


def names(log):
    return [n for n, _ in log if n != "stock_changed"]


class TestReserve:
    def test_reserve_and_available(self, item):
        res = item.reserve(3, order_ref="FC1")
        assert (item.on_hand, item.reserved, item.available) == (10, 3, 7)
        assert res.status == StockReservation.STATUS_PENDING and res.order_ref == "FC1"
        assert res.expires_at > timezone.now()
        assert "7 available" in str(item) and "qty=3" in str(res)

    def test_insufficient(self, item):
        with pytest.raises(InsufficientStockError) as exc:
            item.reserve(11)
        assert exc.value.extra == {"available": 10, "requested": 11, "sku": "RICE-50"}
        item.refresh_from_db()
        assert item.reserved == 0 and StockReservation.objects.count() == 0

    def test_invalid_quantity(self, item):
        with pytest.raises(StockReservationError):
            item.reserve(0)

    def test_oversell_flags(self, item, fc):
        item.allow_oversell = True
        item.save()
        item.reserve(50)
        item.allow_oversell = False
        item.save()
        fc(ALLOW_OVERSELL=True)
        item.reserve(50)
        assert item.reserved == 100 and item.available == 0

    def test_ttl_zero_means_no_expiry(self, item, fc):
        fc(STOCK_RESERVATION_MINUTES=0)
        assert item.reserve(1).expires_at is None
        assert item.reserve(1, ttl_minutes=5).expires_at is not None


class TestReleaseConfirm:
    def test_release_is_idempotent(self, item):
        res = item.reserve(4)
        assert item.release(reservation_id=res.pk) is True
        assert item.release(reservation_id=res.pk) is False
        item.refresh_from_db()
        assert item.reserved == 0
        res.refresh_from_db()
        assert res.status == StockReservation.STATUS_RELEASED
        assert item.release(reservation_id="00000000-0000-0000-0000-000000000000") is False

    def test_release_by_quantity(self, item):
        item.reserve(4)
        item.release(10)
        assert item.reserved == 0  # clamped, never negative

    def test_confirm_sale(self, item):
        res = item.reserve(4, order_ref="FC9")
        assert item.confirm_sale(reservation_id=res.pk) is True
        assert (item.on_hand, item.reserved, item.sold) == (6, 0, 4)
        assert item.confirm_sale(reservation_id=res.pk) is False  # idempotent
        mv = item.movements.filter(movement_type=StockMovement.TYPE_SALE).get()
        assert mv.quantity == -4 and mv.reference == "FC9"

    def test_confirm_after_expiry_still_deducts(self, item):
        res = item.reserve(4)
        item.release(reservation_id=res.pk)  # expired & released by the job
        assert item.confirm_sale(reservation_id=res.pk) is True
        assert (item.on_hand, item.reserved, item.sold) == (6, 0, 4)

    def test_confirm_without_reservation(self, item):
        item.confirm_sale(quantity=2, reference="POS")
        assert (item.on_hand, item.sold) == (8, 2)
        assert item.confirm_sale(reservation_id="00000000-0000-0000-0000-000000000000") is False

    def test_restock_return_adjust(self, item):
        item.return_stock(2, reference="RET1")
        assert item.on_hand == 12
        item.adjust(5, note="stock take")
        assert item.on_hand == 5
        assert sorted(item.movements.values_list("movement_type", "quantity")) == [
            ("adjustment", -7),
            ("restock", 10),
            ("return", 2),
        ]
        with pytest.raises(StockReservationError):
            item.restock(0)
        with pytest.raises(StockReservationError):
            item.adjust(-1)


class TestSignals:
    def test_threshold_crossing_only(self, item, signals_log, fc):
        fc(LOW_STOCK_THRESHOLD=3)
        item.reserve(5)  # 10 -> 5 : above threshold
        assert names(signals_log) == []
        item.reserve(3)  # 5 -> 2 : crosses
        assert names(signals_log) == ["low_stock"]
        item.reserve(1)  # 2 -> 1 : already low, no repeat
        assert names(signals_log) == ["low_stock"]
        item.reserve(1)  # 1 -> 0
        assert names(signals_log) == ["low_stock", "out_of_stock"]
        item.restock(4)  # 0 -> 4
        assert names(signals_log)[-1] == "restocked"
        assert signals_log[-1][1]["quantity"] == 4

    def test_per_item_threshold(self, item, signals_log):
        item.reorder_point = 8
        item.save()
        assert item.low_stock_threshold == 8
        item.reserve(2)
        assert names(signals_log) == ["low_stock"]
        assert item.is_low

    def test_back_in_stock_alerts(self, product, item, user, signals_log):
        item.reserve(10)
        services.subscribe_alert(product, user=user)
        services.subscribe_alert(product, email="Guest@Example.com")
        services.subscribe_alert(product, email="guest@example.com")  # deduplicated
        assert StockAlert.objects.count() == 2
        item.restock(5)
        event = [kw for n, kw in signals_log if n == "back_in_stock"][0]
        assert {a.recipient_email for a in event["alerts"]} == {
            "u@example.com",
            "guest@example.com",
        }
        services.mark_alerts_notified(event["alerts"])
        assert StockAlert.objects.filter(notified_at__isnull=True).count() == 0
        # a new subscription is allowed after the previous one was notified
        services.subscribe_alert(product, user=user)
        assert StockAlert.objects.count() == 3
        assert "StockAlert(" in str(StockAlert.objects.first())


class TestServices:
    def test_untracked_by_default(self, product):
        assert services.get_item(product) is None
        services.check_available(product, 1000)
        assert services.availability_map([product]) == {product.pk: None}
        assert services.reserve_lines([(product, 5)], "FC1") == []

    def test_track_by_default(self, product, fc):
        fc(INVENTORY_TRACK_BY_DEFAULT=True)
        assert services.availability_map([product]) == {product.pk: 0}
        with pytest.raises(InsufficientStockError):
            services.check_available(product, 1)
        assert services.get_item(product) is not None

    def test_availability_map(self, product, item):
        other = Product.objects.create(sku="B")
        oversell = Product.objects.create(sku="C")
        InventoryItem.get_for_product(oversell)
        InventoryItem.objects.filter(object_id=oversell.pk).update(allow_oversell=True)
        assert services.availability_map([product, other, oversell]) == {
            product.pk: 10,
            other.pk: None,
            oversell.pk: None,
        }

    def test_order_lifecycle(self, product, item):
        other = Product.objects.create(sku="B")
        other_item = InventoryItem.get_for_product(other)
        other_item.restock(2)
        services.reserve_lines([(product, 3), (other, 2)], "FC100")
        with pytest.raises(InsufficientStockError):
            services.reserve_lines([(product, 1), (other, 1)], "FC101")
        assert services.confirm_order("FC100") == 2
        assert services.confirm_order("FC100") == 0
        item.refresh_from_db()
        assert (item.on_hand, item.reserved) == (7, 0)

    def test_release_order_and_expired(self, product, item):
        services.reserve_lines([(product, 3)], "FC200")
        assert services.release_order("FC200") == 1
        assert services.release_order("FC200") == 0
        services.reserve_lines([(product, 2)], "FC201")
        StockReservation.objects.filter(order_ref="FC201").update(expires_at=timezone.now() - timedelta(minutes=1))
        assert services.release_expired() == {"released": 1}
        item.refresh_from_db()
        assert item.reserved == 0

    def test_extend_and_return(self, product, item):
        services.reserve_lines([(product, 1)], "FC300")
        services.extend_order_reservations("FC300", 60 * 48)
        res = StockReservation.objects.get(order_ref="FC300")
        assert res.expires_at > timezone.now() + timedelta(hours=47)
        services.extend_order_reservations("FC300", 0)
        res.refresh_from_db()
        assert res.expires_at is None
        services.return_to_stock(product, 2, reference="RMA1")
        item.refresh_from_db()
        assert item.on_hand == 12
        assert services.return_to_stock(Product.objects.create(sku="Z"), 1) is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkouts_never_oversell():
    """20 threads race for 5 units; exactly 5 reservations must succeed."""
    if connection.vendor == "sqlite":
        pytest.skip("SQLite has no concurrent writers; run with FC_TEST_DATABASE_URL=postgresql://...")
    product = Product.objects.create(sku="HOT")
    item = InventoryItem.get_for_product(product)
    item.restock(5)
    ok, failed = [], []
    barrier = threading.Barrier(20)

    def worker():
        try:
            barrier.wait()
            InventoryItem.objects.get(pk=item.pk).reserve(1)
            ok.append(1)
        except InsufficientStockError:
            failed.append(1)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    item.refresh_from_db()
    assert len(ok) == 5 and len(failed) == 15 and item.reserved == 5


class TestCommands:
    def test_reconcile(self, item, fc):
        fc(LOW_STOCK_THRESHOLD=3)
        out = StringIO()
        call_command("reconcile_inventory", stdout=out)
        assert "healthy" in out.getvalue()
        item.reserve(9)
        StockReservation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        out = StringIO()
        call_command("reconcile_inventory", stdout=out)
        assert "Released 1" in out.getvalue()

    def test_reconcile_reports_low(self, item):
        item.reserve(10, ttl_minutes=0)
        out = StringIO()
        call_command("reconcile_inventory", stdout=out)
        assert "RICE-50" in out.getvalue()

    def test_import(self, item, tmp_path):
        path = tmp_path / "stock.csv"
        path.write_text("sku,on_hand,reorder_point\nRICE-50,42,7\nNOPE,1,\nRICE-50,abc,\n", encoding="utf-8")
        out = StringIO()
        call_command("import_inventory", str(path), "--dry-run", stdout=out)
        assert "[DRY RUN] Updated 1" in out.getvalue()
        item.refresh_from_db()
        assert item.on_hand == 10
        out = StringIO()
        call_command("import_inventory", str(path), stdout=out)
        item.refresh_from_db()
        assert (item.on_hand, item.reorder_point) == (42, 7)
        assert "NOPE" in out.getvalue() and "Invalid rows: [4]" in out.getvalue()

    def test_import_missing_file(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("import_inventory", "/definitely/missing.csv")
