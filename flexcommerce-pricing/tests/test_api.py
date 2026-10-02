import uuid
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from flexcommerce_core import hooks
from flexcommerce_core.utils.pricing import get_tax_rate
from flexcommerce_pricing.models import PriceBreakdown, TaxCategory
from tests.models import Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_():
    return APIClient()


@pytest.fixture
def staff_client():
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("s", "s@x.com", "x", is_staff=True))
    return c


class TestCalculate:
    def test_exclusive_default(self, client_):
        r = client_.post("/api/pricing/calculate/", {"price": "10000"}, format="json").data
        assert (r["net"], r["vat"], r["gross"]) == ("10000.00", "750.00", "10750.00")
        assert r["inclusive"] is False and r["formatted_gross"] == "₦10,750.00"

    def test_inclusive_and_custom_rate(self, client_):
        r = client_.post(
            "/api/pricing/calculate/",
            {"price": "1050", "rate": "0.05", "inclusive": True},
            format="json",
        ).data
        assert (r["net"], r["vat"]) == ("1000.00", "50.00")

    @pytest.mark.parametrize(
        "payload",
        [{}, {"price": "abc"}, {"price": "-5"}, {"price": "5", "rate": "1.5"}, {"price": "NaN"}],
    )
    def test_rejects_bad_input_instead_of_500(self, client_, payload):
        assert client_.post("/api/pricing/calculate/", payload, format="json").status_code == 400


class TestQuote:
    def test_quote_uses_live_pipeline(self, client_):
        p = Product.objects.create(price=Decimal("2000"))
        e = Product.objects.create(price=Decimal("500"), vat_exempt=True)

        def ten_off(product, price, **kw):
            return price - 100 if product.pk == p.pk else price

        hooks.register("price.modify", ten_off)
        try:
            r = client_.post(
                "/api/pricing/quote/",
                {
                    "items": [
                        {"product_id": str(p.pk), "quantity": 2},
                        {"product_type": "tests.product", "product_id": str(e.pk)},
                    ]
                },
                format="json",
            ).data
        finally:
            hooks.unregister("price.modify", ten_off)
        assert r["items"][0]["unit_price"] == "1900.00"
        assert r["items"][0]["line_gross"] == "4085.00"
        assert r["items"][1]["line_vat"] == "0.00"
        assert r["subtotal"] == "4585.00" and r["vat"] == "285.00"

    def test_quote_validation(self, client_):
        assert client_.post("/api/pricing/quote/", {"items": []}, format="json").status_code == 400
        missing = client_.post("/api/pricing/quote/", {"items": [{"product_id": str(uuid.uuid4())}]}, format="json")
        assert missing.status_code == 404 and missing.data["error"] == "product_not_found"
        bad_type = client_.post(
            "/api/pricing/quote/",
            {"items": [{"product_type": "auth.user", "product_id": str(uuid.uuid4())}]},
            format="json",
        )
        assert bad_type.status_code == 400 and bad_type.data["error"] == "invalid_product_type"


class TestTaxCategories:
    def test_public_list_staff_write(self, client_, staff_client):
        TaxCategory.objects.create(name="Standard", code="standard")
        data = client_.get("/api/pricing/tax-categories/").data
        assert data[0]["code"] == "standard" and data[0]["effective_rate"] == "0.0750"
        assert client_.post("/api/pricing/tax-categories/", {"name": "X", "code": "x"}).status_code in (401, 403)
        resp = staff_client.post(
            "/api/pricing/tax-categories/",
            {"name": "Luxury", "code": "luxury", "rate": "0.15"},
            format="json",
        )
        assert resp.status_code == 201 and resp.data["effective_rate"] == "0.1500"
        assert (
            staff_client.post(
                "/api/pricing/tax-categories/",
                {"name": "Bad", "code": "bad", "rate": "2"},
                format="json",
            ).status_code
            == 400
        )
        assert staff_client.delete("/api/pricing/tax-categories/luxury/").status_code == 204

    def test_zero_rated_ignores_rate(self):
        cat = TaxCategory.objects.create(
            name="Z", code="z", category_type=TaxCategory.TYPE_ZERO_RATED, rate=Decimal("0.1")
        )
        assert cat.get_rate() == Decimal("0.00")
        cat.rate = Decimal("3")
        with pytest.raises(ValidationError):
            cat.clean()

    def test_tax_category_drives_core_pipeline(self):
        cat = TaxCategory.objects.create(name="Reduced", code="reduced", rate=Decimal("0.05"))

        class P:
            tax_category = cat

        assert get_tax_rate(P()) == Decimal("0.05")


class TestInfra:
    def test_price_breakdown_from_product_uses_hooks(self):
        p = Product.objects.create(price=Decimal("1000"))
        b = PriceBreakdown.from_product(p, quantity=3)
        assert b.line_gross == Decimal("3225.00") and str(b).startswith("PriceBreakdown(")

    def test_migrations_complete(self):
        call_command("makemigrations", "flexcommerce_pricing", "--check", "--dry-run", stdout=StringIO())

    def test_admin(self, client):
        client.force_login(get_user_model().objects.create_superuser("a", "a@x.com", "x"))
        for m in ("taxcategory", "pricebreakdown"):
            assert client.get(f"/admin/flexcommerce_pricing/{m}/").status_code == 200
