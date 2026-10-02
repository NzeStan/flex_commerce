import uuid
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from flexcommerce_inventory.models import InventoryItem, StockAlert
from tests.models import Product

pytestmark = pytest.mark.django_db


class TestStaffAPI:
    def test_create_restock_adjust_movements(self, staff_client, product):
        resp = staff_client.post(
            "/api/inventory/",
            {"product_id": str(product.pk), "on_hand": 20, "reorder_point": 3},
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["sku"] == "RICE-50" and data["available"] == 20 and data["product_type"] == "tests.product"
        item_id = data["id"]
        dup = staff_client.post("/api/inventory/", {"product_id": str(product.pk)}, format="json")
        assert dup.status_code == 400
        assert (
            staff_client.post(
                f"/api/inventory/{item_id}/restock/",
                {"quantity": 5, "reference": "PO-1"},
                format="json",
            ).json()["on_hand"]
            == 25
        )
        assert (
            staff_client.post(f"/api/inventory/{item_id}/restock/", {"quantity": 0}, format="json").status_code == 400
        )
        assert (
            staff_client.post(
                f"/api/inventory/{item_id}/adjust/", {"on_hand": 7, "note": "count"}, format="json"
            ).json()["on_hand"]
            == 7
        )
        movements = staff_client.get(f"/api/inventory/{item_id}/movements/").json()
        # (timestamps can tie on coarse clocks, so compare as a multiset)
        assert sorted(m["movement_type"] for m in movements["results"]) == ["adjustment", "restock", "restock"]
        assert {m["quantity"] for m in movements["results"]} == {20, 5, -18}
        assert (
            staff_client.patch(
                f"/api/inventory/{item_id}/", {"reorder_point": 10, "on_hand": 999}, format="json"
            ).json()["on_hand"]
            == 7
        )  # stock can't be edited directly

    def test_list_filters_and_reservations(self, staff_client, item, fc):
        fc(PRODUCT_MODELS=["tests.Product"], LOW_STOCK_THRESHOLD=5)
        other = InventoryItem.get_for_product(Product.objects.create(sku="BEANS"))
        other.restock(100)
        assert staff_client.get("/api/inventory/?search=rice").json()["count"] == 1
        assert staff_client.get("/api/inventory/?low_stock=1").json()["count"] == 0
        item.reserve(6, order_ref="FC77")
        low = staff_client.get("/api/inventory/?low_stock=1").json()
        assert [i["sku"] for i in low["results"]] == ["RICE-50"]
        other.reorder_point = 200
        other.save()
        assert staff_client.get("/api/inventory/?low_stock=1").json()["count"] == 2
        res = staff_client.get("/api/inventory/reservations/?status=pending&order_ref=FC77").json()
        assert res["count"] == 1 and res["results"][0]["quantity"] == 6

    def test_create_validation(self, staff_client):
        assert staff_client.post("/api/inventory/", {"product_id": str(uuid.uuid4())}, format="json").status_code == 404
        assert staff_client.post("/api/inventory/", {"product_id": "x"}, format="json").status_code == 400

    def test_customers_blocked(self, user):
        c = APIClient()
        c.force_authenticate(user)
        assert c.get("/api/inventory/").status_code == 403


class TestAlertsAPI:
    def test_anonymous_needs_email(self, product):
        c = APIClient()
        assert c.post("/api/inventory/alerts/", {"product_id": str(product.pk)}, format="json").status_code == 400
        resp = c.post(
            "/api/inventory/alerts/",
            {"product_id": str(product.pk), "email": "a@b.com"},
            format="json",
        )
        assert resp.status_code == 201 and StockAlert.objects.get().email == "a@b.com"
        assert c.get("/api/inventory/alerts/").status_code in (401, 403)

    def test_user_alerts(self, user, product):
        c = APIClient()
        c.force_authenticate(user)
        alert = c.post("/api/inventory/alerts/", {"product_id": str(product.pk)}, format="json").json()
        assert c.get("/api/inventory/alerts/").json()["count"] == 1
        assert c.delete(f"/api/inventory/alerts/{alert['id']}/").status_code == 204
        assert StockAlert.objects.count() == 0

    def test_throttled(self, product, fc):
        fc(PRODUCT_MODELS=["tests.Product"], THROTTLE_RATES={"stock_alert": "1/minute"})
        c = APIClient()
        c.post(
            "/api/inventory/alerts/",
            {"product_id": str(product.pk), "email": "a@b.com"},
            format="json",
        )
        assert (
            c.post(
                "/api/inventory/alerts/",
                {"product_id": str(product.pk), "email": "c@d.com"},
                format="json",
            ).status_code
            == 429
        )


class TestInfra:
    def test_migrations_and_admin(self, client, item, user, product):
        call_command("makemigrations", "flexcommerce_inventory", "--check", "--dry-run", stdout=StringIO())
        item.reserve(1)
        StockAlert.objects.create(content_type=item.content_type, object_id=product.pk, user=user)
        client.force_login(get_user_model().objects.create_superuser("root", "r@x.com", "x"))
        for m in ("inventoryitem", "stockreservation", "stockmovement", "stockalert"):
            assert client.get(f"/admin/flexcommerce_inventory/{m}/").status_code == 200
        assert client.get(f"/admin/flexcommerce_inventory/inventoryitem/{item.pk}/change/").status_code == 200
        assert "RICE-50" in str(item) and "restock" in str(item.movements.first())
