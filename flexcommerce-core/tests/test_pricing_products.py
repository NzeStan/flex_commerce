import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from flexcommerce_core import hooks
from flexcommerce_core.exceptions import (
    InvalidProductTypeError,
    PricingError,
    ProductNotFoundError,
    ProductUnavailableError,
)
from flexcommerce_core.utils import products
from flexcommerce_core.utils.helpers import HandlerRegistry, registry
from flexcommerce_core.utils.pricing import (
    DefaultPriceHandler,
    DefaultTaxHandler,
    allocate_discount,
    get_tax_rate,
    get_unit_price,
    line_amounts,
    price_product,
)
from tests.models import Product


class TestPriceAndTax:
    def test_default_price_handler(self):
        assert DefaultPriceHandler().get_price(SimpleNamespace(price="1500.50")) == Decimal("1500.50")

    def test_missing_price_raises(self):
        with pytest.raises(PricingError):
            DefaultPriceHandler().get_price(SimpleNamespace(pk=1))

    @pytest.mark.parametrize(
        "product,rate",
        [
            (SimpleNamespace(), Decimal("0.075")),
            (SimpleNamespace(vat_exempt=True), Decimal("0.00")),
            (SimpleNamespace(vat_zero_rated=True), Decimal("0.00")),
            (
                SimpleNamespace(tax_category=SimpleNamespace(get_rate=lambda: Decimal("0.05"))),
                Decimal("0.05"),
            ),
            (None, Decimal("0.075")),
        ],
    )
    def test_tax_rates(self, fc, product, rate):
        fc(VAT_RATE=0.075)
        assert DefaultTaxHandler().get_rate(product) == rate
        assert get_tax_rate(product) == rate

    def test_legacy_compute(self, fc):
        fc(VAT_RATE=0.075, VAT_INCLUSIVE=False)
        assert DefaultTaxHandler().compute(Decimal("100"))["gross"] == Decimal("107.50")

    def test_price_modify_hook(self):
        def half_price(product, price, **kwargs):
            return price / 2

        hooks.register("price.modify", half_price)
        try:
            assert get_unit_price(SimpleNamespace(price=Decimal("1000"))) == Decimal("500.00")
        finally:
            hooks.unregister("price.modify", half_price)

    def test_negative_price_clamped(self):
        def negative(product, price, **kwargs):
            return Decimal("-5")

        hooks.register("price.modify", negative)
        try:
            assert get_unit_price(SimpleNamespace(price=Decimal("1"))) == Decimal("0.00")
        finally:
            hooks.unregister("price.modify", negative)

    def test_line_amounts_exclusive(self):
        r = line_amounts(Decimal("1000"), 3, Decimal("0.075"), inclusive=False)
        assert r["unit_gross"] == Decimal("1075.00")
        assert r["line_net"] == Decimal("3000.00")
        assert r["line_vat"] == Decimal("225.00")
        assert r["line_gross"] == Decimal("3225.00")

    def test_line_amounts_inclusive(self):
        r = line_amounts(Decimal("1075"), 2, Decimal("0.075"), inclusive=True)
        assert r["line_gross"] == Decimal("2150.00")
        assert r["line_net"] + r["line_vat"] == r["line_gross"]

    def test_price_product(self, fc):
        fc(VAT_RATE=0.075, VAT_INCLUSIVE=False)
        r = price_product(SimpleNamespace(price=Decimal("200"), vat_exempt=True), quantity=2)
        assert r["line_gross"] == Decimal("400.00") and r["line_vat"] == Decimal("0.00")

    def test_allocate_discount(self):
        r = allocate_discount(Decimal("1075"), Decimal("75"), Decimal("107.50"))
        assert r == {
            "total": Decimal("967.50"),
            "vat": Decimal("67.50"),
            "discount": Decimal("107.50"),
        }

    def test_allocate_discount_clamps(self):
        assert allocate_discount(Decimal("100"), Decimal("7"), Decimal("500"))["total"] == Decimal("0.00")
        assert allocate_discount(Decimal("100"), Decimal("7"), Decimal("-5"))["discount"] == Decimal("0.00")
        assert allocate_discount(Decimal("0"), Decimal("0"), Decimal("5"))["total"] == Decimal("0.00")


class CustomPrice:
    def get_price(self, product, **kwargs):
        return Decimal("42")


class TestRegistry:
    def test_resolution_order(self, fc):
        reg = HandlerRegistry()
        reg.set_default("X", DefaultPriceHandler)
        assert isinstance(reg.get("X"), DefaultPriceHandler)
        fc(X="tests.test_pricing_products.CustomPrice")
        assert isinstance(reg.get("X"), CustomPrice)
        reg.register("X", DefaultTaxHandler)
        assert isinstance(reg.get("X"), DefaultTaxHandler)
        reg.unregister("X")
        assert isinstance(reg.get("X"), CustomPrice)

    def test_missing(self):
        with pytest.raises(KeyError):
            HandlerRegistry().get("NOPE")
        assert HandlerRegistry().get("NOPE", default=CustomPrice).get_price(None) == Decimal("42")

    def test_price_handler_setting_used_by_pipeline(self, fc):
        fc(PRICE_HANDLER="tests.test_pricing_products.CustomPrice")
        assert get_unit_price(SimpleNamespace(price=1)) == Decimal("42.00")
        assert registry.get_class("PRICE_HANDLER") is CustomPrice


@pytest.mark.django_db
class TestProducts:
    def test_resolve_by_label_and_model_name(self):
        assert products.resolve_product_model("tests.product") is Product
        assert products.resolve_product_model("Product") is Product
        assert products.resolve_product_model("tests_product") is Product
        assert products.resolve_product_model(None) is Product

    def test_rejects_non_whitelisted_models(self):
        with pytest.raises(InvalidProductTypeError):
            products.resolve_product_model("auth.user")

    def test_requires_type_with_multiple_models(self, fc):
        fc(PRODUCT_MODELS=["tests.Product", "tests.SoftItem"])
        with pytest.raises(InvalidProductTypeError):
            products.resolve_product_model("")
        # ...but get_product finds the row across all configured models
        from tests.models import SoftItem

        item = SoftItem.objects.create()
        assert products.get_product(None, item.pk) == item
        with pytest.raises(ProductNotFoundError):
            products.get_product("", uuid.uuid4())

    def test_get_product_no_models(self, fc):
        fc(PRODUCT_MODELS=[])
        with pytest.raises(InvalidProductTypeError):
            products.get_product(None, uuid.uuid4())

    def test_no_models_configured(self, fc):
        fc(PRODUCT_MODELS=[])
        with pytest.raises(InvalidProductTypeError):
            products.resolve_product_model("tests.product")

    def test_string_setting(self, fc):
        fc(PRODUCT_MODELS="tests.Product")
        assert products.get_product_models() == [Product]

    def test_get_product(self):
        p = Product.objects.create()
        assert products.get_product("tests.product", p.pk) == p
        assert products.get_product("tests.product", str(p.pk)) == p

    @pytest.mark.parametrize("bad_id", [uuid.uuid4(), "not-a-uuid", None])
    def test_get_product_not_found(self, bad_id):
        with pytest.raises(ProductNotFoundError):
            products.get_product("tests.product", bad_id)

    def test_inactive_product_not_purchasable(self):
        p = Product.objects.create(is_active=False)
        with pytest.raises(ProductUnavailableError):
            products.get_product("tests.product", p.pk)
        assert products.get_product("tests.product", p.pk, for_purchase=False) == p

    def test_is_purchasable_callable(self):
        assert products.is_purchasable(SimpleNamespace(is_purchasable=lambda: False)) is False
        assert products.is_purchasable(SimpleNamespace()) is True

    def test_attribute_helpers(self):
        p = SimpleNamespace(name="Phone", sku="SKU1", vendor_id="v", weight="1.5", category_ids=lambda: [1, 2])
        assert products.product_name(p) == "Phone"
        assert products.product_sku(p) == "SKU1"
        assert products.product_vendor_id(p) == "v"
        assert products.product_weight(p) == Decimal("1.5")
        assert products.product_category_ids(p) == {"1", "2"}
        assert products.product_category_ids(SimpleNamespace(category_id=9)) == {"9"}
        assert products.product_category_ids(SimpleNamespace()) == set()
        assert products.product_weight(SimpleNamespace(weight="bad")) == Decimal("0")

    def test_prefetch_products(self, django_assert_max_num_queries):
        from django.contrib.contenttypes.models import ContentType

        ps = [Product.objects.create() for _ in range(5)]
        ct = ContentType.objects.get_for_model(Product)
        rows = [SimpleNamespace(content_type_id=ct.pk, object_id=p.pk) for p in ps]
        with django_assert_max_num_queries(2):
            found = products.prefetch_products(rows)
        assert {obj.pk for obj in found.values()} == {p.pk for p in ps}

    def test_load_model_invalid(self):
        with pytest.raises(ValueError):
            products.load_model("nodots")
        assert products.is_product_instance(Product()) is True
        assert products.product_type_label(Product) == "tests.product"
