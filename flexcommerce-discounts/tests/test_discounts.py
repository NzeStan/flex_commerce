import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from flexcommerce_core.exceptions import (
    CouponExpiredError,
    CouponMinimumValueError,
    CouponNotApplicableError,
    CouponNotFoundError,
    CouponUsageLimitError,
)
from flexcommerce_core.utils.pricing import get_unit_price
from flexcommerce_discounts import services
from flexcommerce_discounts.models import (
    Coupon,
    CouponRedemption,
    CouponUsage,
    FlashSale,
    FlashSaleItem,
)
from tests.models import Product

from .conftest import make_lines

pytestmark = pytest.mark.django_db
D = Decimal


def coupon(**kwargs):
    data = {
        "code": "save10",
        "name": "Save",
        "coupon_type": Coupon.TYPE_PERCENTAGE,
        "value": D("10"),
    }
    data.update(kwargs)
    return Coupon.objects.create(**data)


class TestCouponModel:
    def test_code_normalised_and_str(self):
        c = coupon(code="  naija25 ")
        assert c.code == "NAIJA25" and "NAIJA25" in str(c)

    def test_is_valid(self):
        now = timezone.now()
        assert coupon().is_valid
        assert not coupon(code="a", is_active=False).is_valid
        assert not coupon(code="b", starts_at=now + timedelta(days=1)).is_valid
        assert not coupon(code="c", expires_at=now - timedelta(days=1)).is_valid
        assert not coupon(code="d", usage_limit=1, used_count=1).is_valid

    def test_calculate_discount_legacy_api(self):
        assert coupon().calculate_discount(D("1000")) == D("100.00")
        assert coupon(code="cap", max_discount=D("50")).calculate_discount(D("1000")) == D("50.00")
        assert coupon(code="f", coupon_type=Coupon.TYPE_FIXED, value=D("5000")).calculate_discount(D("1000")) == D(
            "1000.00"
        )
        assert coupon(code="s", coupon_type=Coupon.TYPE_FREE_SHIPPING).calculate_discount(D("1000")) == D("0.00")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"value": D("0")},
            {"value": D("101")},
            {"coupon_type": Coupon.TYPE_FIXED, "value": D("0")},
            {"coupon_type": Coupon.TYPE_BUY_X_GET_Y, "buy_quantity": 0, "get_quantity": 1},
        ],
    )
    def test_clean(self, kwargs):
        with pytest.raises(ValidationError):
            Coupon(
                code="x",
                name="x",
                **{"coupon_type": Coupon.TYPE_PERCENTAGE, "value": D("10"), **kwargs},
            ).clean()

    def test_clean_dates(self):
        now = timezone.now()
        with pytest.raises(ValidationError):
            Coupon(code="x", name="x", value=D("5"), starts_at=now, expires_at=now).clean()


class TestEvaluate:
    def test_percentage_with_allocation(self):
        lines = make_lines(("1000", 2), ("3000", 1))
        r = services.evaluate(coupon(), lines)
        assert r.amount == D("500.00")
        assert r.allocations == {0: D("200.00"), 1: D("300.00")}
        assert r.to_dict()["discount_amount"] == "500.00" and r.code == "SAVE10"

    def test_fixed_never_exceeds_total(self):
        r = services.evaluate(coupon(coupon_type=Coupon.TYPE_FIXED, value=D("99999")), make_lines(("1000", 1)))
        assert r.amount == D("1000.00")

    def test_free_shipping(self):
        r = services.evaluate(coupon(coupon_type=Coupon.TYPE_FREE_SHIPPING), make_lines(("1000", 1)))
        assert r.free_shipping and r.amount == D("0.00")

    def test_minimum(self):
        with pytest.raises(CouponMinimumValueError) as exc:
            services.evaluate(coupon(minimum_cart_value=D("5000")), make_lines(("1000", 1)))
        assert exc.value.extra == {"minimum": "5000.00", "current": "1000.00"}

    def test_buy_2_get_1_cheapest_free(self):
        c = coupon(coupon_type=Coupon.TYPE_BUY_X_GET_Y, buy_quantity=2, get_quantity=1, value=D("0"))
        lines = make_lines(("5000", 2), ("1000", 2))  # 4 units -> 1 free (the cheapest)
        r = services.evaluate(c, lines)
        assert r.amount == D("1000.00") and r.allocations == {1: D("1000.00")}
        lines = make_lines(("5000", 3), ("1000", 3))  # 6 units -> 2 free
        assert services.evaluate(c, lines).amount == D("2000.00")
        with pytest.raises(CouponNotApplicableError):
            services.evaluate(c, make_lines(("5000", 2)))

    def test_product_category_vendor_restrictions(self):
        vendor = uuid.uuid4()
        lines = make_lines(("1000", 1, {"category_id": "phones"}), ("2000", 1, {"vendor_id": vendor}), ("4000", 1))
        by_cat = services.evaluate(coupon(code="cat", restricted_categories=["phones"]), lines)
        assert by_cat.amount == D("100.00") and set(by_cat.allocations) == {0}
        by_product = services.evaluate(coupon(code="prod", restricted_products=[lines[2].product_id]), lines)
        assert by_product.amount == D("400.00")
        by_vendor = services.evaluate(coupon(code="ven", vendor_id=vendor), lines)
        assert by_vendor.amount == D("200.00")
        excluded = services.evaluate(coupon(code="exc", excluded_products=[lines[2].product_id]), lines)
        assert excluded.amount == D("300.00")
        with pytest.raises(CouponNotApplicableError):
            services.evaluate(coupon(code="none", restricted_products=[str(uuid.uuid4())]), lines)

    def test_parent_product_targeting(self):
        (line,) = make_lines(("1000", 1))
        line.parent_id = "parent-product-id"
        assert services.is_line_eligible(coupon(restricted_products=["parent-product-id"]), line)

    def test_expired_and_limits(self, user):
        with pytest.raises(CouponExpiredError):
            services.evaluate(
                coupon(code="old", expires_at=timezone.now() - timedelta(minutes=1)),
                make_lines(("1", 1)),
            )
        with pytest.raises(CouponExpiredError):
            services.evaluate(
                coupon(code="soon", starts_at=timezone.now() + timedelta(days=1)),
                make_lines(("1", 1)),
            )
        with pytest.raises(CouponUsageLimitError):
            services.evaluate(coupon(code="used", usage_limit=1, used_count=1), make_lines(("1", 1)))
        c = coupon(code="once")
        CouponRedemption.objects.create(coupon=c, customer_key=f"user:{user.pk}", count=1)
        with pytest.raises(CouponUsageLimitError):
            services.evaluate(c, make_lines(("1", 1)), user=user)
        # the check can be skipped for previews
        assert services.evaluate(c, make_lines(("1000", 1)), user=user, check=False).amount == D("100.00")

    def test_get_coupon(self):
        coupon()
        assert services.get_coupon(" save10 ").code == "SAVE10"
        with pytest.raises(CouponNotFoundError):
            services.get_coupon("nope")
        with pytest.raises(CouponNotFoundError):
            services.get_coupon("")

    def test_best_automatic(self):
        coupon(code="auto5", value=D("5"), auto_apply=True)
        coupon(code="auto15", value=D("15"), auto_apply=True, minimum_cart_value=D("10000"))
        coupon(code="manual50", value=D("50"))
        lines = make_lines(("1000", 1))
        assert services.best_automatic(lines, D("1000")).code == "AUTO5"
        big = make_lines(("20000", 1))
        assert services.best_automatic(big, D("20000")).code == "AUTO15"
        Coupon.objects.all().delete()
        assert services.best_automatic(lines, D("1000")) is None


class TestRedeem:
    def test_redeem_and_restore(self, user):
        c = coupon(usage_limit=5, per_user_limit=2)
        u = services.redeem("save10", order_ref="FC1", amount=D("100"), user=user)
        assert u.email == "buyer@example.com" and "FC1" in str(u)
        services.redeem("save10", order_ref="FC2", amount=D("100"), user=user)
        with pytest.raises(CouponUsageLimitError):
            services.redeem("save10", order_ref="FC3", user=user)
        c.refresh_from_db()
        assert c.used_count == 2
        assert services.restore("FC1") == 1
        assert services.restore("FC1") == 0
        c.refresh_from_db()
        assert c.used_count == 1
        assert CouponRedemption.objects.get().count == 1
        services.redeem("save10", order_ref="FC4", user=user)  # allowed again

    def test_global_limit(self):
        coupon(usage_limit=1, per_user_limit=0)
        services.redeem("save10", order_ref="A", email="a@x.com")
        with pytest.raises(CouponUsageLimitError):
            services.redeem("save10", order_ref="B", email="b@x.com")

    def test_unlimited_counts_after_commit(self):
        c = coupon(per_user_limit=0)
        services.redeem("save10", order_ref="A")
        services.redeem("save10", order_ref="B")
        c.refresh_from_db()
        assert c.used_count == 2 and CouponUsage.objects.count() == 2

    def test_guest_email_limit(self):
        coupon(per_user_limit=1)
        services.redeem("save10", order_ref="A", email="Guest@Example.com")
        with pytest.raises(CouponUsageLimitError):
            services.redeem("save10", order_ref="B", email="guest@example.com")

    def test_legacy_record_usage(self, user):
        from types import SimpleNamespace

        coupon()
        cart = SimpleNamespace(discount_amount=D("50"), user=user, session_key="abc")
        usage = services.CouponService.record_usage("SAVE10", cart, order_ref="X")
        assert usage.discount_applied == D("50") and usage.session_key == "abc"


class TestFlashSales:
    def window(self, **kwargs):
        now = timezone.now()
        return FlashSale.objects.create(
            name="Black Friday",
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
            **kwargs,
        )

    def test_legacy_percentage_sale(self):
        p = Product.objects.create(price=D("1000"))
        sale = self.window(discount_percentage=D("20"))
        assert sale.is_running and "20" in str(sale)
        assert get_unit_price(p) == D("800.00")
        assert services.CouponService.get_active_flash_sale(p) == sale
        sale.applicable_products = [str(uuid.uuid4())]
        sale.save()
        assert get_unit_price(p) == D("1000.00")
        assert services.CouponService.get_active_flash_sale(p) is None

    def test_item_sale_price_and_cap(self):
        from django.contrib.contenttypes.models import ContentType

        p = Product.objects.create(price=D("1000"))
        sale = self.window(discount_percentage=D("10"))
        item = FlashSaleItem.objects.create(
            sale=sale,
            content_type=ContentType.objects.get_for_model(p),
            object_id=p.pk,
            sale_price=D("499"),
            quantity_limit=2,
        )
        assert get_unit_price(p) == D("499.00")
        assert item.remaining == 2 and item.price_for(D("300")) == D("300")
        FlashSaleItem.objects.filter(pk=item.pk).update(sold_quantity=2)
        services.invalidate_caches()
        assert get_unit_price(p) == D("1000.00")  # sold out -> normal price
        other = Product.objects.create(price=D("1000"))
        assert get_unit_price(other) == D("1000.00")  # sale has items: others unaffected

    def test_percentage_item(self):
        from django.contrib.contenttypes.models import ContentType

        p = Product.objects.create(price=D("1000"))
        sale = self.window(discount_percentage=D("25"))
        item = FlashSaleItem.objects.create(
            sale=sale, content_type=ContentType.objects.get_for_model(p), object_id=p.pk
        )
        assert get_unit_price(p) == D("750.00") and item.remaining is None and str(item).startswith("Black Friday")

    def test_not_running(self):
        p = Product.objects.create(price=D("1000"))
        now = timezone.now()
        FlashSale.objects.create(
            name="Later",
            discount_percentage=D("50"),
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=2),
        )
        assert get_unit_price(p) == D("1000.00")

    def test_clean(self):
        now = timezone.now()
        with pytest.raises(ValidationError):
            FlashSale(name="x", starts_at=now, ends_at=now).clean()
        with pytest.raises(ValidationError):
            FlashSale(name="x", starts_at=now, ends_at=now + timedelta(1), discount_percentage=D("150")).clean()
        assert FlashSale(name="x", discount_percentage=D("10")).apply_to_price(D("100")) == D("90.00")


class TestAPI:
    def test_validate_endpoint(self, client):
        coupon(minimum_cart_value=D("5000"))
        ok = client.post("/api/coupons/validate/", {"code": "save10", "cart_total": "10000"}).json()
        assert ok["valid"] and ok["discount_amount"] == "1000.00"
        low = client.post("/api/coupons/validate/", {"code": "save10", "cart_total": "100"}).json()
        assert low == {
            "valid": False,
            "error": "coupon_minimum_value",
            "detail": low["detail"],
            "extra": {"minimum": "5000.00"},
        }
        missing = client.post("/api/coupons/validate/", {"code": "nope"}).json()
        assert missing["valid"] is False and missing["error"] == "coupon_not_found"
        assert "discount_amount" not in client.post("/api/coupons/validate/", {"code": "save10"}).json()

    def test_validate_is_throttled(self, client, fc):
        fc(PRODUCT_MODELS=["tests.Product"], THROTTLE_RATES={"coupon": "3/minute"})
        codes = [client.post("/api/coupons/validate/", {"code": "x"}).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]

    def test_staff_crud(self, client, staff_client):
        assert client.get("/api/coupons/").status_code in (401, 403)
        resp = staff_client.post("/api/coupons/", {"code": "new20", "name": "New", "value": "20"}, format="json")
        assert resp.status_code == 201 and resp.data["code"] == "NEW20"
        dup = staff_client.post("/api/coupons/", {"code": "NEW20", "name": "Dup", "value": "5"}, format="json")
        assert dup.status_code == 400
        bad = staff_client.post("/api/coupons/", {"code": "bad", "name": "Bad", "value": "150"}, format="json")
        assert bad.status_code == 400
        cid = resp.data["id"]
        assert staff_client.patch(f"/api/coupons/{cid}/", {"value": "30"}, format="json").status_code == 200
        assert staff_client.get("/api/coupons/?search=new").data["count"] == 1
        services.redeem("NEW20", order_ref="O1", email="a@b.com")
        assert staff_client.get(f"/api/coupons/{cid}/usages/").data["count"] == 1

    def test_flash_sale_api(self, client, staff_client):
        now = timezone.now()
        resp = staff_client.post(
            "/api/flash-sales/",
            {
                "name": "Midnight",
                "discount_percentage": "30",
                "starts_at": (now - timedelta(minutes=5)).isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        sale_id = resp.data["id"]
        p = Product.objects.create(price=D("2000"))
        item = staff_client.post(
            f"/api/flash-sales/{sale_id}/items/",
            {"product_id": str(p.pk), "sale_price": "999", "quantity_limit": 10},
            format="json",
        )
        assert item.status_code == 201 and item.data["remaining"] == 10
        active = client.get("/api/flash-sales/active/").data
        assert active[0]["items"][0]["product_type"] == "tests.product"
        assert get_unit_price(p) == D("999.00")
        assert staff_client.delete(f"/api/flash-sales/{sale_id}/items/{item.data['id']}/").status_code == 204
        assert get_unit_price(p) == D("1400.00")
        bad = staff_client.post(
            "/api/flash-sales/",
            {
                "name": "Bad",
                "discount_percentage": "30",
                "starts_at": now.isoformat(),
                "ends_at": now.isoformat(),
            },
            format="json",
        )
        assert bad.status_code == 400
        bad_pct = staff_client.patch(f"/api/flash-sales/{sale_id}/", {"discount_percentage": "130"}, format="json")
        assert bad_pct.status_code == 400

    def test_migrations_and_admin(self, client):
        from django.contrib.auth import get_user_model

        call_command("makemigrations", "flexcommerce_discounts", "--check", "--dry-run", stdout=StringIO())
        client.force_login(get_user_model().objects.create_superuser("root", "r@x.com", "x"))
        c = coupon()
        CouponRedemption.objects.create(coupon=c, customer_key="email:x@y.com")
        for m in ("coupon", "flashsale", "couponredemption"):
            assert client.get(f"/admin/flexcommerce_discounts/{m}/").status_code == 200
        assert client.get(f"/admin/flexcommerce_discounts/coupon/{c.pk}/change/").status_code == 200
        assert "SAVE10" in str(CouponRedemption.objects.get())
