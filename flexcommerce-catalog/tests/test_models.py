from decimal import Decimal

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError

from flexcommerce_catalog.models import (
    CATEGORY_TREE_CACHE_KEY,
    Brand,
    Category,
    Product,
    ProductImage,
    ProductVariant,
    unique_slug,
)
from flexcommerce_core.utils.pricing import get_tax_rate, get_unit_price
from flexcommerce_core.utils.products import get_product_models, is_purchasable, product_name

from .conftest import make_product

pytestmark = pytest.mark.django_db


class TestCategory:
    def test_path_and_depth(self, phones):
        root = phones.parent
        assert root.depth == 0 and phones.depth == 1
        assert phones.path.startswith(root.path)
        assert str(phones) == "Electronics › Phones"
        assert [c.name for c in phones.get_ancestors()] == ["Electronics"]
        assert set(root.get_descendants()) == {root, phones}
        assert list(root.get_descendants(include_self=False)) == [phones]

    def test_slug_unique(self):
        a = Category.objects.create(name="Phones & Tablets")
        b = Category.objects.create(name="Phones & Tablets")
        assert a.slug == "phones-tablets" and b.slug == "phones-tablets-2"

    def test_moving_category_reroots_subtree(self, phones):
        android = Category.objects.create(name="Android", parent=phones)
        new_root = Category.objects.create(name="Mobile")
        phones.parent = new_root
        phones.save()
        android.refresh_from_db()
        assert android.path == f"{new_root.pk.hex}/{phones.pk.hex}/{android.pk.hex}/"
        assert android.depth == 2
        # Move back to top level
        phones.parent = None
        phones.save()
        android.refresh_from_db()
        assert android.depth == 1 and android.path == f"{phones.pk.hex}/{android.pk.hex}/"

    def test_cycle_and_depth_validation(self, phones, fc):
        root = phones.parent
        root.parent = phones
        with pytest.raises(ValidationError):
            root.clean()
        fc(CATALOG_MAX_CATEGORY_DEPTH=2)
        deep = Category(name="Too deep", parent=phones)
        with pytest.raises(ValidationError):
            deep.clean()

    def test_tree_cache_invalidated(self, phones):
        cache.set(CATEGORY_TREE_CACHE_KEY, ["stale"])
        phones.name = "Smartphones"
        phones.save()
        assert cache.get(CATEGORY_TREE_CACHE_KEY) is None
        cache.set(CATEGORY_TREE_CACHE_KEY, ["stale"])
        Category.objects.create(name="Temp").delete()
        assert cache.get(CATEGORY_TREE_CACHE_KEY) is None


class TestProduct:
    def test_slug_and_publish(self, brand):
        p = Product.objects.create(name="Spark 10", brand=brand)
        assert p.slug == "spark-10" and p.published_at is None
        p.status = Product.STATUS_ACTIVE
        p.save(update_fields=["status"])
        p.refresh_from_db()
        assert p.published_at is not None
        assert Brand.objects.get().slug == "tecno"

    def test_price_range_denormalised(self):
        p = make_product(price="1000")
        ProductVariant.objects.create(product=p, sku="B", price=Decimal("3000"), compare_at_price=Decimal("4000"))
        p.refresh_from_db()
        assert (p.min_price, p.max_price, p.compare_at_price) == (Decimal("1000"), Decimal("3000"), None)
        assert p.discount_percent == 0
        ProductVariant.objects.create(product=p, sku="C", price=Decimal("800"), compare_at_price=Decimal("1000"))
        p.refresh_from_db()
        assert (p.min_price, p.compare_at_price, p.discount_percent) == (Decimal("800"), Decimal("1000"), 20)
        ProductVariant.objects.filter(sku__in=["B", "C"]).delete()  # queryset delete bypasses save hooks
        p.refresh_price_range()
        p.refresh_from_db()
        assert p.max_price == Decimal("1000") and p.compare_at_price is None

    def test_inactive_variants_excluded_from_range(self):
        p = make_product(price="1000")
        v = ProductVariant.objects.create(product=p, sku="C", price=Decimal("10"), is_active=False)
        p.refresh_from_db()
        assert p.min_price == Decimal("1000")
        assert v.discount_percent == 0

    def test_default_variant(self):
        p = make_product()
        second = ProductVariant.objects.create(product=p, sku="X", price=1)
        assert p.default_variant.is_default
        p.variants.update(is_default=False)
        assert p.default_variant is not None
        p.variants.update(is_active=False)
        assert p.default_variant is None
        assert second.pk in p.purchasable_ids()

    def test_queryset_helpers(self, product, phones):
        make_product(name="Draft", status=Product.STATUS_DRAFT)
        assert list(Product.objects.active()) == [product]
        assert list(Product.objects.in_category(phones.parent)) == [product]

    def test_tax_category_without_pricing_app(self):
        p = make_product(tax_category_code="standard")
        assert p.tax_category is None


class TestVariantProtocol:
    def test_variant_is_the_default_product_model(self):
        assert get_product_models() == [ProductVariant]

    def test_protocol(self, product):
        v = product.variants.get()
        assert product_name(v) == "Camon 20"
        v.name = "Blue / 128GB"
        assert v.display_name == "Camon 20 - Blue / 128GB"
        assert str(v) == v.display_name
        assert is_purchasable(v)
        assert v.vendor_id is None and v.vat_exempt is False and v.tax_category is None
        assert v.category_ids() == product.category_ids()
        assert get_unit_price(v) == Decimal("150000.00")
        assert get_tax_rate(v) == Decimal("0.075")

    def test_not_purchasable_when_product_not_active(self, product):
        v = product.variants.get()
        product.status = Product.STATUS_ARCHIVED
        product.save()
        v.refresh_from_db()
        assert not is_purchasable(v)

    def test_vat_exempt_flows_from_product(self):
        p = make_product(vat_exempt=True)
        assert get_tax_rate(p.variants.get()) == Decimal("0")

    def test_negative_price_invalid(self, product):
        v = product.variants.get()
        v.price = Decimal("-1")
        with pytest.raises(ValidationError):
            v.clean()


class TestImages:
    def test_url_or_file_required(self, product):
        with pytest.raises(ValidationError):
            ProductImage(product=product).clean()
        img = product.images.get()
        assert img.get_url() == "https://cdn.example.com/p.jpg"
        assert str(img) == "https://cdn.example.com/p.jpg"

    def test_unique_slug_helper(self):
        assert unique_slug(Brand, "!!!") == "item"
