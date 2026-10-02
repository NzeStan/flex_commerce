import threading
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from flexcommerce_cart import signals as cart_signals
from flexcommerce_cart.middleware import CartMiddleware
from flexcommerce_cart.models import Cart, CartItem, SavedItem
from flexcommerce_cart.services import (
    CartPricingService,
    CartService,
    CartSessionManager,
    expire_carts,
    notify_abandoned_carts,
)
from flexcommerce_core.exceptions import (
    CartError,
    CartItemNotFoundError,
    CartLockedError,
    InsufficientStockError,
    ProductUnavailableError,
)
from tests.models import Product

pytestmark = pytest.mark.django_db
D = Decimal
ADD = "/api/cart/add/"


def add(client, product, qty=1, **extra):
    return client.post(ADD, {"product_id": str(product.pk), "quantity": qty}, format="json", **extra)


class TestAnonymousSessionCart:
    def test_get_does_not_create_rows(self, api):
        data = api.get("/api/cart/").json()
        assert data["item_count"] == 0 and data["cart_token"] is None
        assert Cart.objects.count() == 0

    def test_add_creates_cart_and_prices_it(self, api, product):
        resp = add(api, product, 2)
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["item_count"] == 2
        assert data["subtotal"] == "21500.00"  # 2 x (10000 + 7.5% VAT)
        assert data["tax_total"] == "1500.00" and data["total"] == "21500.00"
        item = data["items"][0]
        assert item["product"] == {
            "type": "tests.product",
            "id": str(product.pk),
            "name": "Indomie Carton",
            "sku": "INDO-40",
        }
        assert (item["unit_net"], item["vat_amount"], item["unit_price_with_tax"]) == (
            "10000.00",
            "750.00",
            "10750.00",
        )
        assert resp["X-Cart-Token"] == data["cart_token"]
        # the same browser session sees the same cart
        assert api.get("/api/cart/").json()["id"] == data["id"]
        cart = Cart.objects.get()
        assert cart.session_key and cart.expires_at

    def test_add_same_product_twice_merges_line(self, api, product):
        add(api, product, 1)
        data = add(api, product, 2).json()
        assert len(data["items"]) == 1 and data["item_count"] == 3

    def test_update_remove_clear(self, api, product, product2):
        add(api, product)
        data = add(api, product2, 3).json()
        milk = next(i for i in data["items"] if i["product_id"] == str(product2.pk))
        data = api.patch(f"/api/cart/{milk['id']}/update/", {"quantity": 1}, format="json").json()
        assert data["item_count"] == 2
        data = api.patch(f"/api/cart/{milk['id']}/update/", {"quantity": 0}, format="json").json()
        assert data["item_count"] == 1
        indomie = data["items"][0]["id"]
        assert api.delete(f"/api/cart/{indomie}/remove/").json()["item_count"] == 0
        add(api, product)
        assert api.post("/api/cart/clear/").json()["items"] == []

    def test_missing_items_and_empty_cart_errors(self, api):
        assert (
            api.patch(
                "/api/cart/00000000-0000-0000-0000-000000000000/update/",
                {"quantity": 1},
                format="json",
            ).status_code
            == 404
        )
        assert api.delete("/api/cart/not-a-uuid/remove/").status_code == 404
        assert api.post("/api/cart/clear/").json()["item_count"] == 0

    def test_validation(self, api, product):
        assert api.post(ADD, {"product_id": "x"}, format="json").status_code == 400
        assert api.post(ADD, {"product_id": str(product.pk), "quantity": 0}, format="json").status_code == 400
        resp = api.post(ADD, {"product_id": str(product.pk), "product_type": "auth.user"}, format="json")
        assert resp.status_code == 400 and resp.json()["error"] == "invalid_product_type"
        resp = api.post(ADD, {"product_id": "00000000-0000-0000-0000-000000000000"}, format="json")
        assert resp.status_code == 404
        # "product_model" alias still works
        resp = api.post(ADD, {"product_id": str(product.pk), "product_model": "tests.Product"}, format="json")
        assert resp.status_code == 201

    def test_unavailable_product(self, api, product):
        product.is_active = False
        product.save()
        assert add(api, product).json()["error"] == "product_unavailable"


class TestTokenCart:
    """Mobile / SPA clients without cookies."""

    def test_token_header_round_trip(self, product, product2):
        first = APIClient()
        token = add(first, product).json()["cart_token"]
        other_device = APIClient(HTTP_X_CART_TOKEN=token)
        assert other_device.get("/api/cart/").json()["item_count"] == 1
        add(other_device, product2)
        assert other_device.get("/api/cart/").json()["item_count"] == 2
        assert Cart.objects.count() == 1

    def test_token_only_client_creates_sessionless_cart(self, product):
        c = APIClient(HTTP_X_CART_TOKEN="unknown-token")
        data = add(c, product).json()
        assert data["cart_token"] != "unknown-token"
        assert Cart.objects.get().session_key == ""

    def test_custom_header_name(self, product, fc):
        fc(PRODUCT_MODELS=["tests.Product"], CART_TOKEN_HEADER="X-Basket")
        resp = add(APIClient(), product)
        assert resp["X-Basket"] == resp.json()["cart_token"]


class TestUserCartAndMerge:
    def test_user_cart(self, auth_api, user, product):
        data = add(auth_api, product).json()
        cart = Cart.objects.get()
        assert cart.user == user and cart.email == user.email and data["id"] == str(cart.pk)
        assert auth_api.get("/api/cart/").json()["item_count"] == 1

    def test_merge_on_real_login(self, user, product, product2, fc):
        fc(PRODUCT_MODELS=["tests.Product"], CART_MAX_QUANTITY_PER_ITEM=5)
        existing = Cart.objects.create(user=user)
        CartService(existing).add_item(product, 4)
        browser = APIClient()
        add(browser, product, 3)
        add(browser, product2, 1)
        anon = Cart.objects.get(user__isnull=True)
        assert browser.login(username="buyer", password="pass1234")  # rotates the session key
        data = browser.get("/api/cart/").json()
        assert data["id"] == str(existing.pk)
        quantities = {i["product_id"]: i["quantity"] for i in data["items"]}
        assert quantities == {str(product.pk): 5, str(product2.pk): 1}  # capped at 5
        anon.refresh_from_db()
        assert anon.status == Cart.STATUS_MERGED and anon.items.count() == 0

    def test_merge_via_token_after_login(self, user, product):
        token = add(APIClient(), product, 2).json()["cart_token"]
        mobile = APIClient(HTTP_X_CART_TOKEN=token)
        mobile.force_authenticate(user)
        data = mobile.get("/api/cart/").json()
        assert data["item_count"] == 2
        assert Cart.objects.get(user=user).items.count() == 1

    def test_merge_disabled(self, user, product, fc):
        fc(PRODUCT_MODELS=["tests.Product"], CART_MERGE_ON_LOGIN=False)
        browser = APIClient()
        add(browser, product)
        browser.login(username="buyer", password="pass1234")
        assert not Cart.objects.filter(user=user).exists()

    def test_login_merge_never_breaks_login(self, user, monkeypatch):
        from flexcommerce_cart import services

        monkeypatch.setattr(services.CartSessionManager, "handle_login_merge", classmethod(lambda *a: 1 / 0))
        assert APIClient().login(username="buyer", password="pass1234")

    def test_merge_edge_cases(self, user, product):
        cart = Cart.objects.create(user=user)
        assert CartService(cart).merge_with(cart) == cart
        other = Cart.objects.create(session_key="s1", status=Cart.STATUS_ORDERED)
        assert CartService(cart).merge_with(other) == cart
        anon = Cart.objects.create(session_key="s2", coupon_code="HELLO")
        CartItem.objects.create(
            cart=anon,
            content_type=ContentType.objects.get_for_model(Product),
            object_id=product.pk,
            quantity=2,
        )
        CartService(cart).merge_with(anon)
        cart.refresh_from_db()
        assert cart.items_count == 2
        assert cart.coupon_code == "" and cart.coupon_error == "Discounts are not enabled."


class TestPricing:
    def test_vat_inclusive_mode(self, api, product, fc):
        fc(PRODUCT_MODELS=["tests.Product"], VAT_INCLUSIVE=True)
        data = add(api, product).json()
        assert data["subtotal"] == "10000.00" and data["tax_total"] == "697.67"
        item = data["items"][0]
        assert (item["unit_net"], item["vat_amount"]) == ("9302.33", "697.67")

    def test_exempt_products(self, api):
        rice = Product.objects.create(price=D("5000"), vat_exempt=True)
        data = add(api, rice).json()
        assert data["tax_total"] == "0.00" and data["total"] == "5000.00"

    def test_price_changes_are_picked_up(self, api, product):
        add(api, product)
        Product.objects.filter(pk=product.pk).update(price=D("12000"))
        cart = Cart.objects.get()
        cart.recalculate()
        cart.refresh_from_db()
        assert cart.total_amount == D("12900.00")

    def test_deleted_or_disabled_products_removed_with_notice(self, api, product, product2):
        add(api, product)
        add(api, product2)
        Product.objects.filter(pk=product2.pk).update(is_active=False)
        cart = Cart.objects.get()
        cart.recalculate()
        data = api.get("/api/cart/").json()
        assert len(data["items"]) == 1
        assert data["notices"] == ["Peak Milk is no longer available and was removed."]
        Product.objects.filter(pk=product.pk).delete()
        cart.recalculate()
        assert api.get("/api/cart/").json()["notices"] == ["An item is no longer available and was removed."]
        cart.recalculate()
        assert api.get("/api/cart/").json()["notices"] == []

    def test_recalculate_query_count(self, api, django_assert_max_num_queries):
        for i in range(10):
            add(api, Product.objects.create(price=D(1000 + i)))
        cart = Cart.objects.get()
        with django_assert_max_num_queries(8):
            CartPricingService(cart).recalculate()


class TestLimitsAndStock:
    def test_quantity_limit(self, api, product, fc):
        fc(PRODUCT_MODELS=["tests.Product"], CART_MAX_QUANTITY_PER_ITEM=3)
        add(api, product, 2)
        resp = add(api, product, 2)
        assert resp.status_code == 400 and resp.json()["error"] == "cart_limit_exceeded"

    def test_distinct_items_limit(self, api, fc):
        fc(PRODUCT_MODELS=["tests.Product"], CART_MAX_ITEMS=2)
        add(api, Product.objects.create())
        add(api, Product.objects.create())
        assert add(api, Product.objects.create()).json()["error"] == "cart_limit_exceeded"

    def test_legacy_stock_attribute(self, api):
        scarce = Product.objects.create(stock=2)
        assert add(api, scarce, 3).json()["extra"] == {"available": 2, "requested": 3}
        item = add(api, scarce, 2).json()["items"][0]
        assert api.patch(f"/api/cart/{item['id']}/update/", {"quantity": 5}, format="json").status_code == 400

    def test_oversell_setting(self, api, fc):
        fc(PRODUCT_MODELS=["tests.Product"], ALLOW_OVERSELL=True)
        assert add(api, Product.objects.create(stock=0), 5).status_code == 201

    def test_locked_and_inactive_carts(self, user, product):
        cart = Cart.objects.create(user=user, status=Cart.STATUS_LOCKED)
        with pytest.raises(CartLockedError):
            CartService(cart).add_item(product)
        cart.status = Cart.STATUS_ORDERED
        cart.save()
        with pytest.raises(CartError) as exc:
            CartService(cart).add_item(product)
        assert exc.value.code == "cart_inactive"

    def test_service_level_validation(self, user, product):
        cart = Cart.objects.create(user=user)
        service = CartService(cart)
        with pytest.raises(CartError):
            service._validate_quantity(0)
        product.is_active = False
        with pytest.raises(ProductUnavailableError):
            service.add_item(product)
        with pytest.raises(CartItemNotFoundError):
            service.update_quantity("00000000-0000-0000-0000-000000000000", 1)
        with pytest.raises(InsufficientStockError):
            service.add_item(Product.objects.create(stock=0))

    def test_update_item_whose_product_was_deleted(self, user, product):
        cart = Cart.objects.create(user=user)
        item = CartService(cart).add_item(product)
        Product.objects.filter(pk=product.pk).delete()
        with pytest.raises(CartItemNotFoundError):
            CartService(cart).update_quantity(item.pk, 2)


class TestCouponsWithoutDiscountsApp:
    def test_coupon_endpoint_reports_not_installed(self, api, product):
        add(api, product)
        resp = api.post("/api/cart/coupon/", {"code": "SAVE"}, format="json")
        assert resp.status_code == 400 and resp.json()["error"] == "discounts_not_installed"
        assert api.delete("/api/cart/coupon/remove/").status_code == 200


class TestSaveForLaterAndContact:
    def test_save_restore_delete(self, auth_api, user, product):
        item_id = add(auth_api, product, 2).json()["items"][0]["id"]
        saved = auth_api.post(f"/api/cart/{item_id}/save/").json()
        assert saved["saved_price"] == "10750.00" and saved["product"]["name"] == "Indomie Carton"
        assert auth_api.get("/api/cart/").json()["item_count"] == 0
        assert len(auth_api.get("/api/cart/saved/").json()) == 1
        data = auth_api.post(f"/api/cart/{saved['id']}/restore/").json()
        assert data["item_count"] == 1 and SavedItem.objects.count() == 0
        item_id = data["items"][0]["id"]
        saved = auth_api.post(f"/api/cart/{item_id}/save/").json()
        assert auth_api.delete(f"/api/cart/saved/{saved['id']}/").status_code == 204
        assert SavedItem.objects.count() == 0

    def test_requires_login(self, api, product):
        item_id = add(api, product).json()["items"][0]["id"]
        assert api.post(f"/api/cart/{item_id}/save/").status_code in (401, 403)
        assert api.get("/api/cart/saved/").status_code in (401, 403)

    def test_service_guards(self, user, product):
        anon_cart = Cart.objects.create(session_key="x")
        with pytest.raises(CartError):
            CartService(anon_cart).save_for_later("x")
        cart = Cart.objects.create(user=user)
        with pytest.raises(CartItemNotFoundError):
            CartService(cart).restore_saved_item("bad")
        from django.contrib.contenttypes.models import ContentType

        ghost = SavedItem.objects.create(
            user=user,
            content_type=ContentType.objects.get_for_model(Product),
            object_id="00000000-0000-0000-0000-000000000000",
        )
        with pytest.raises(CartItemNotFoundError):
            CartService(cart).restore_saved_item(ghost.pk)
        assert not SavedItem.objects.exists()
        assert "SavedItem(" in str(ghost)

    def test_contact(self, api, product):
        data = api.post(
            "/api/cart/contact/",
            {"email": "guest@example.com", "phone": "08031234567"},
            format="json",
        ).json()
        assert data["email"] == "guest@example.com"
        assert api.post("/api/cart/contact/", {"phone": "12"}, format="json").status_code == 400


class TestSignals:
    def test_events(self, api, product):
        events = []
        for name in ("item_added", "item_updated", "item_removed", "cart_cleared"):
            getattr(cart_signals, name).connect(
                lambda sender, _n=name, **kw: events.append(_n),
                weak=False,
                dispatch_uid=f"t-{name}",
            )
        try:
            item = add(api, product).json()["items"][0]["id"]
            api.patch(f"/api/cart/{item}/update/", {"quantity": 2}, format="json")
            api.delete(f"/api/cart/{item}/remove/")
            api.post("/api/cart/clear/")
        finally:
            for name in ("item_added", "item_updated", "item_removed", "cart_cleared"):
                getattr(cart_signals, name).disconnect(dispatch_uid=f"t-{name}")
        assert events == ["item_added", "item_updated", "item_removed", "cart_cleared"]


class TestJobs:
    def test_expire_and_purge(self, product):
        old = Cart.objects.create(session_key="old", expires_at=timezone.now() - timedelta(days=1))
        fresh = Cart.objects.create(session_key="fresh", expires_at=timezone.now() + timedelta(days=1))
        assert expire_carts() == {"expired": 1, "purged": 0}
        old.refresh_from_db()
        fresh.refresh_from_db()
        assert old.status == Cart.STATUS_EXPIRED and fresh.status == Cart.STATUS_ACTIVE
        Cart.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=200))
        assert expire_carts() == {"expired": 0, "purged": 1}
        assert not Cart.objects.filter(pk=old.pk).exists()

    def test_abandoned_notifications(self, user, product):
        got = []
        cart_signals.cart_abandoned.connect(
            lambda sender, cart, **kw: got.append(cart.pk), weak=False, dispatch_uid="t-ab"
        )
        try:
            idle = Cart.objects.create(user=user)
            CartService(idle).add_item(product)
            guest = Cart.objects.create(session_key="g", email="g@example.com")
            CartService(guest).add_item(product)
            nobody = Cart.objects.create(session_key="n")
            CartService(nobody).add_item(product)
            Cart.objects.update(last_activity=timezone.now() - timedelta(hours=5))
            assert notify_abandoned_carts() == {"notified": 2}
            assert notify_abandoned_carts() == {"notified": 0}  # only once
        finally:
            cart_signals.cart_abandoned.disconnect(dispatch_uid="t-ab")
        assert set(got) == {idle.pk, guest.pk}

    def test_command(self, product):
        Cart.objects.create(session_key="old", expires_at=timezone.now() - timedelta(days=1))
        out = StringIO()
        call_command("expire_carts", "--dry-run", stdout=out)
        assert "Would expire 1" in out.getvalue()
        call_command("expire_carts", stdout=out)
        assert "Expired 1 carts" in out.getvalue()


class TestModelAndInfra:
    def test_model_helpers(self, user):
        cart = Cart.objects.create(user=user)
        assert str(cart).startswith("Cart(") and cart.is_active and not cart.is_expired
        cart.lock()
        assert cart.is_locked
        cart.unlock()
        cart.mark_abandoned()
        cart.mark_expired()
        cart.mark_ordered()
        assert cart.status == Cart.STATUS_ORDERED
        cart.set_expiry()
        assert cart.expires_at > timezone.now()
        assert (cart.item_count, cart.subtotal, cart.total, cart.tax_total) == (0, 0, 0, 0)

    def test_item_properties(self, user, product):
        cart = Cart.objects.create(user=user)
        item = CartService(cart).add_item(product, 2)
        assert item.line_total == D("21500.00") and item.line_vat == D("1500.00") and item.line_net == D("20000.00")
        assert "× 2" in str(item)

    def test_middleware_is_lazy(self, django_assert_num_queries):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=False)
        request.session = {}
        with django_assert_num_queries(0):
            CartMiddleware(lambda r: "ok")(request)
        assert request.cart is not None  # evaluated lazily
        assert bool(request.cart) is False

    def test_session_manager_legacy_api(self, product):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=False)
        from django.contrib.sessions.backends.db import SessionStore

        request.session = SessionStore()
        cart = CartSessionManager.get_or_create_cart(request)
        assert cart.session_key and CartSessionManager.get_session_key() == "flexcommerce_cart"
        assert CartSessionManager.get_or_create_cart(request) == cart

    def test_migrations_and_admin(self, client, user, product):
        call_command("makemigrations", "flexcommerce_cart", "--check", "--dry-run", stdout=StringIO())
        cart = Cart.objects.create(user=user)
        CartService(cart).add_item(product)
        admin = get_user_model().objects.create_superuser("root", "r@x.com", "x")
        client.force_login(admin)
        assert client.get("/admin/flexcommerce_cart/cart/").status_code == 200
        assert client.get(f"/admin/flexcommerce_cart/cart/{cart.pk}/change/").status_code == 200
        client.post(
            "/admin/flexcommerce_cart/cart/",
            {"action": "mark_expired", "_selected_action": [str(cart.pk)]},
        )
        cart.refresh_from_db()
        assert cart.status == Cart.STATUS_EXPIRED
        client.post(
            "/admin/flexcommerce_cart/cart/",
            {"action": "mark_abandoned", "_selected_action": [str(cart.pk)]},
        )
        assert client.get("/admin/flexcommerce_cart/saveditem/").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_parallel_adds_to_one_cart_are_serialised():
    if connection.vendor == "sqlite":
        pytest.skip("needs PostgreSQL (FC_TEST_DATABASE_URL)")
    product = Product.objects.create(stock=1000)
    cart = Cart.objects.create(session_key="race")
    barrier = threading.Barrier(10)
    errors = []

    def worker():
        try:
            barrier.wait()
            CartService(Cart.objects.get(pk=cart.pk)).add_item(Product.objects.get(pk=product.pk), 1)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert CartItem.objects.get(cart=cart).quantity == 10
    cart.refresh_from_db()
    assert cart.items_count == 10
