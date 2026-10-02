import uuid
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from flexcommerce_catalog.models import Brand, Category, Product, ProductImage, ProductVariant
from flexcommerce_core import hooks

from .conftest import make_product

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/catalog/products/"


@pytest.fixture
def vendor_hook(user):
    vendor_id = uuid.uuid4()

    def hook(u):
        return vendor_id if u.pk == user.pk else None

    hooks.register("catalog.vendor_id_for_user", hook)
    yield vendor_id
    hooks.unregister("catalog.vendor_id_for_user", hook)


def results(resp):
    assert resp.status_code == 200, resp.data
    return [p["slug"] for p in resp.data["results"]]


class TestCategoriesAndBrands:
    def test_list_tree_and_detail(self, api_client, phones):
        Category.objects.create(name="Hidden", is_active=False)
        flat = api_client.get("/api/catalog/categories/").data
        assert {c["slug"] for c in flat} == {"electronics", "phones"}
        tree = api_client.get("/api/catalog/categories/?tree=1").data
        assert tree[0]["slug"] == "electronics" and tree[0]["children"][0]["slug"] == "phones"
        # cached response is reused
        assert api_client.get("/api/catalog/categories/?tree=1").data == tree
        roots = api_client.get("/api/catalog/categories/?parent=root").data
        assert [c["slug"] for c in roots] == ["electronics"]
        kids = api_client.get("/api/catalog/categories/?parent=electronics").data
        assert [c["slug"] for c in kids] == ["phones"]
        assert api_client.get("/api/catalog/categories/phones/").data["depth"] == 1

    def test_staff_write_only(self, api_client, auth_client, staff_client, phones):
        assert api_client.post("/api/catalog/categories/", {"name": "X"}).status_code in (401, 403)
        assert auth_client.post("/api/catalog/categories/", {"name": "X"}).status_code == 403
        resp = staff_client.post("/api/catalog/categories/", {"name": "Tablets", "parent": str(phones.parent.pk)})
        assert resp.status_code == 201 and resp.data["slug"] == "tablets" and resp.data["depth"] == 1

    def test_cannot_move_category_under_its_child(self, staff_client, phones):
        resp = staff_client.patch(
            f"/api/catalog/categories/{phones.parent.slug}/", {"parent": str(phones.pk)}, format="json"
        )
        assert resp.status_code == 400

    def test_depth_limit(self, staff_client, phones, fc):
        fc(CATALOG_MAX_CATEGORY_DEPTH=2)
        assert staff_client.post("/api/catalog/categories/", {"name": "D", "parent": str(phones.pk)}).status_code == 400

    def test_brands(self, api_client, staff_client, brand):
        Brand.objects.create(name="Samsung", is_official_store=True)
        Brand.objects.create(name="Gone", is_active=False)
        assert {b["slug"] for b in api_client.get("/api/catalog/brands/").data["results"]} == {"tecno", "samsung"}
        assert [b["slug"] for b in api_client.get("/api/catalog/brands/?official=1").data["results"]] == ["samsung"]
        assert staff_client.post("/api/catalog/brands/", {"name": "Infinix"}).status_code == 201


class TestProductListing:
    def test_only_active_products_public(self, api_client, product):
        make_product(name="Draft phone", status=Product.STATUS_DRAFT)
        data = api_client.get(PRODUCTS).data
        assert [p["slug"] for p in data["results"]] == [product.slug]
        item = data["results"][0]
        assert item["image"] == "https://cdn.example.com/p.jpg"
        assert item["brand"] == "tecno" and item["brand_name"] == "Tecno"
        assert Decimal(item["min_price"]) == Decimal("150000")

    def test_filters(self, api_client, product, phones, brand):
        other_brand = Brand.objects.create(name="Itel")
        cheap = make_product(name="Itel A70", price="60000", brand=other_brand, categories=[phones], is_featured=True)
        laptop = make_product(name="Laptop", price="400000", categories=[Category.objects.create(name="Laptops")])
        assert set(results(api_client.get(PRODUCTS + "?category=electronics"))) == {product.slug, cheap.slug}
        assert results(api_client.get(PRODUCTS + "?category=nope")) == []
        assert results(api_client.get(PRODUCTS + "?brand=itel")) == [cheap.slug]
        assert set(results(api_client.get(PRODUCTS + "?brand=itel,tecno"))) == {cheap.slug, product.slug}
        assert results(api_client.get(PRODUCTS + "?min_price=300000")) == [laptop.slug]
        assert results(api_client.get(PRODUCTS + "?max_price=70000")) == [cheap.slug]
        assert results(api_client.get(PRODUCTS + "?featured=1")) == [cheap.slug]
        assert api_client.get(PRODUCTS + "?min_price=abc").status_code == 400
        assert results(api_client.get(PRODUCTS + f"?vendor={uuid.uuid4()}")) == []
        assert results(api_client.get(PRODUCTS + "?vendor=bad")) == []

    def test_on_sale_rating_in_stock(self, api_client, product):
        sale = make_product(name="Sale item", price="900")
        sale.variants.update(compare_at_price=Decimal("1000"))
        sale.refresh_price_range()
        Product.objects.filter(pk=product.pk).update(rating_avg=Decimal("4.5"), in_stock=False)
        assert results(api_client.get(PRODUCTS + "?on_sale=1")) == [sale.slug]
        assert results(api_client.get(PRODUCTS + "?rating=4")) == [product.slug]
        assert results(api_client.get(PRODUCTS + "?in_stock=1")) == [sale.slug]

    def test_attribute_filter(self, api_client, product):
        ProductVariant.objects.create(product=product, sku="RED", price=1, attributes={"color": "Red"})
        other = make_product(name="Other")
        assert results(api_client.get(PRODUCTS + "?attr_color=Red")) == [product.slug]
        # lookup injection attempts are ignored rather than executed
        assert set(results(api_client.get(PRODUCTS + "?attr_color__contains=R"))) == {product.slug, other.slug}

    def test_search(self, api_client, product):
        make_product(name="Fridge")
        assert results(api_client.get(PRODUCTS + "?q=camera")) == [product.slug]
        assert results(api_client.get(PRODUCTS + "?q=tecno camon")) == [product.slug]
        sku = product.variants.get().sku
        assert results(api_client.get(PRODUCTS + f"?q={sku}")) == [product.slug]

    def test_custom_search_handler(self, api_client, product, fc):
        fc(CATALOG_SEARCH_HANDLER="tests.test_api.nothing_search")
        assert results(api_client.get(PRODUCTS + "?q=camon")) == []

    def test_ordering(self, api_client, product):
        cheap = make_product(name="Cheap", price="10")
        Product.objects.filter(pk=cheap.pk).update(sold_count=5, rating_avg=Decimal("5"))
        assert results(api_client.get(PRODUCTS + "?ordering=price"))[0] == cheap.slug
        assert results(api_client.get(PRODUCTS + "?ordering=-price"))[0] == product.slug
        assert results(api_client.get(PRODUCTS + "?ordering=popularity"))[0] == cheap.slug
        assert results(api_client.get(PRODUCTS + "?ordering=rating"))[0] == cheap.slug
        assert results(api_client.get(PRODUCTS + "?ordering=name"))[0] == product.slug

    def test_facets(self, api_client, product, phones):
        make_product(name="Cheap", price="10", brand=Brand.objects.create(name="Itel"), categories=[phones])
        data = api_client.get("/api/catalog/products/facets/?category=phones&min_price=999999").data
        assert data["price"] == {"min": Decimal("10.00"), "max": Decimal("150000.00")}
        assert {b["slug"] for b in data["brands"]} == {"tecno", "itel"}

    def test_query_count_is_bounded(self, api_client, django_assert_max_num_queries, phones, brand):
        for i in range(15):
            p = make_product(name=f"P{i}", brand=brand, categories=[phones])
            ProductImage.objects.create(product=p, url=f"https://cdn/{i}.jpg")
        with django_assert_max_num_queries(6):
            assert len(api_client.get(PRODUCTS + "?page_size=15").data["results"]) == 15


def nothing_search(queryset, query):
    return queryset.none()


class TestProductDetail:
    def test_by_slug_and_id(self, api_client, product):
        by_slug = api_client.get(f"{PRODUCTS}{product.slug}/").data
        by_id = api_client.get(f"{PRODUCTS}{product.pk}/").data
        assert by_slug["id"] == by_id["id"] == str(product.pk)
        assert by_slug["breadcrumbs"] == [
            {"name": "Electronics", "slug": "electronics"},
            {"name": "Phones", "slug": "phones"},
        ]
        assert len(by_slug["variants"]) == 1 and "cost_price" not in by_slug["variants"][0]
        assert by_slug["variants"][0]["in_stock"] is None  # inventory app not installed
        assert by_slug["categories"][0]["slug"] == "phones"

    def test_404s(self, api_client, product):
        assert api_client.get(f"{PRODUCTS}missing/").status_code == 404
        assert api_client.get(f"{PRODUCTS}{uuid.uuid4()}/").status_code == 404
        draft = make_product(name="Secret", status=Product.STATUS_DRAFT)
        assert api_client.get(f"{PRODUCTS}{draft.slug}/").status_code == 404

    def test_inactive_variants_hidden_from_public(self, api_client, staff_client, product):
        ProductVariant.objects.create(product=product, sku="OFF", price=5, is_active=False)
        assert len(api_client.get(f"{PRODUCTS}{product.slug}/").data["variants"]) == 1
        staff_view = staff_client.get(f"{PRODUCTS}{product.slug}/").data
        assert len(staff_view["variants"]) == 2 and "cost_price" in staff_view["variants"][0]

    def test_related(self, api_client, product, phones):
        sibling = make_product(name="Sibling", categories=[phones])
        make_product(name="Unrelated")
        assert [p["slug"] for p in api_client.get(f"{PRODUCTS}{product.slug}/related/").data] == [sibling.slug]

    def test_no_breadcrumbs_without_category(self, api_client):
        p = make_product(name="Loose")
        assert api_client.get(f"{PRODUCTS}{p.slug}/").data["breadcrumbs"] == []


class TestProductWrite:
    def test_anonymous_and_customers_cannot_write(self, api_client, auth_client):
        payload = {"name": "X", "price": "10", "sku": "X1"}
        assert api_client.post(PRODUCTS, payload).status_code in (401, 403)
        assert auth_client.post(PRODUCTS, payload).status_code == 403

    def test_staff_create_single_variant(self, staff_client, phones, brand):
        resp = staff_client.post(
            PRODUCTS,
            {
                "name": "Pova 5",
                "price": "180000",
                "sku": "POVA5",
                "status": "active",
                "categories": [str(phones.pk)],
                "brand": str(brand.pk),
                "compare_at_price": "200000",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["variants"][0]["sku"] == "POVA5"
        p = Product.objects.get(slug="pova-5")
        assert p.min_price == Decimal("180000") and p.discount_percent == 10

    def test_staff_create_with_variants(self, staff_client):
        resp = staff_client.post(
            PRODUCTS,
            {
                "name": "T-shirt",
                "variants": [
                    {"sku": "TS-S", "price": "5000", "attributes": {"size": "S"}},
                    {"sku": "TS-L", "price": "6000", "attributes": {"size": "L"}},
                ],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        p = Product.objects.get(slug="t-shirt")
        assert p.variants.count() == 2 and p.default_variant.sku == "TS-S"
        assert (p.min_price, p.max_price) == (Decimal("5000"), Decimal("6000"))

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "No price"},
            {"name": "Dup", "variants": [{"sku": "A", "price": "1"}, {"sku": "A", "price": "2"}]},
            {"name": "Neg", "price": "-1", "sku": "NEG"},
        ],
    )
    def test_validation(self, staff_client, payload):
        assert staff_client.post(PRODUCTS, payload, format="json").status_code == 400

    def test_duplicate_sku_rejected(self, staff_client, product):
        sku = product.variants.get().sku
        assert staff_client.post(PRODUCTS, {"name": "D", "price": "1", "sku": sku}, format="json").status_code == 400
        resp = staff_client.post(PRODUCTS, {"name": "D", "variants": [{"sku": sku, "price": "1"}]}, format="json")
        assert resp.status_code == 400

    def test_update_and_delete(self, staff_client, product):
        resp = staff_client.patch(f"{PRODUCTS}{product.slug}/", {"name": "Camon 20 Pro", "price": "1"}, format="json")
        assert resp.status_code == 200 and resp.data["name"] == "Camon 20 Pro"
        product.refresh_from_db()
        assert product.min_price == Decimal("150000")  # price ignored on update
        assert staff_client.delete(f"{PRODUCTS}{product.slug}/").status_code == 204

    def test_staff_sees_drafts_and_can_filter_status(self, staff_client, product):
        draft = make_product(name="Draft", status=Product.STATUS_DRAFT)
        assert results(staff_client.get(PRODUCTS + "?status=draft")) == [draft.slug]


class TestVendorAccess:
    def test_vendor_creates_and_manages_own_products(self, auth_client, vendor_hook, product):
        resp = auth_client.post(PRODUCTS, {"name": "Vendor Phone", "price": "1000", "sku": "VP1"}, format="json")
        assert resp.status_code == 201, resp.data
        mine = Product.objects.get(slug="vendor-phone")
        assert mine.vendor_id == vendor_hook
        # vendor can see own drafts via ?mine=1
        assert results(auth_client.get(PRODUCTS + "?mine=1")) == [mine.slug]
        assert auth_client.patch(f"{PRODUCTS}{mine.slug}/", {"name": "Renamed"}, format="json").status_code == 200
        # but not other products
        assert auth_client.patch(f"{PRODUCTS}{product.slug}/", {"name": "Hacked"}, format="json").status_code == 404
        assert auth_client.delete(f"{PRODUCTS}{product.slug}/").status_code == 404

    def test_vendor_cannot_spoof_vendor_id(self, auth_client, vendor_hook):
        other = uuid.uuid4()
        auth_client.post(PRODUCTS, {"name": "S", "price": "1", "sku": "S1", "vendor_id": str(other)}, format="json")
        assert Product.objects.get(slug="s").vendor_id == vendor_hook

    def test_variants_and_images(self, auth_client, staff_client, vendor_hook, product):
        mine = make_product(name="Mine", vendor_id=vendor_hook)
        ok = auth_client.post("/api/catalog/variants/", {"product": str(mine.pk), "sku": "M2", "price": "5"})
        assert ok.status_code == 201, ok.data
        assert "cost_price" not in ok.data
        denied = auth_client.post("/api/catalog/variants/", {"product": str(product.pk), "sku": "Z", "price": "5"})
        assert denied.status_code == 403
        assert auth_client.post("/api/catalog/variants/", {"sku": "Q", "price": "1"}).status_code == 400
        listed = auth_client.get(f"/api/catalog/variants/?product={mine.pk}").data["results"]
        assert {v["sku"] for v in listed} == {mine.variants.first().sku, "M2"}

        img = auth_client.post(
            "/api/catalog/images/",
            {"product": str(mine.pk), "url": "https://cdn.example.com/1.jpg", "is_primary": True},
        )
        assert img.status_code == 201, img.data
        img2 = auth_client.post(
            "/api/catalog/images/",
            {"product": str(mine.pk), "url": "https://cdn.example.com/2.jpg", "is_primary": True},
        )
        assert ProductImage.objects.get(pk=img.data["id"]).is_primary is False
        assert ProductImage.objects.get(pk=img2.data["id"]).is_primary is True
        assert auth_client.post("/api/catalog/images/", {"product": str(mine.pk)}).status_code == 400
        assert auth_client.post("/api/catalog/images/", {"url": "https://cdn.example.com/3.jpg"}).status_code == 400
        assert (
            auth_client.post(
                "/api/catalog/images/", {"product": str(product.pk), "url": "https://cdn.example.com/x.jpg"}
            ).status_code
            == 403
        )
        assert len(auth_client.get(f"/api/catalog/images/?product={mine.pk}").data["results"]) == 2
        assert staff_client.get("/api/catalog/images/").data["count"] == 3

    def test_image_file_upload(self, staff_client, product, settings, tmp_path):
        from django.core.files.uploadedfile import SimpleUploadedFile

        settings.MEDIA_ROOT = str(tmp_path)
        upload = SimpleUploadedFile("p.png", b"\x89PNG\r\n", content_type="image/png")
        resp = staff_client.post(
            "/api/catalog/images/", {"product": str(product.pk), "image": upload}, format="multipart"
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["src"].startswith("http://testserver/")


class TestInfrastructure:
    def test_migrations_complete(self):
        call_command("makemigrations", "flexcommerce_catalog", "--check", "--dry-run", stdout=StringIO())

    def test_admin(self, client, product):
        from django.contrib.auth import get_user_model

        admin = get_user_model().objects.create_superuser("root", "r@x.com", "x")
        client.force_login(admin)
        for model in ("category", "brand", "product", "productvariant"):
            assert client.get(f"/admin/flexcommerce_catalog/{model}/").status_code == 200
        assert client.get(f"/admin/flexcommerce_catalog/product/{product.pk}/change/").status_code == 200
        client.post(
            "/admin/flexcommerce_catalog/product/", {"action": "archive", "_selected_action": [str(product.pk)]}
        )
        product.refresh_from_db()
        assert product.status == Product.STATUS_ARCHIVED
        client.post(
            "/admin/flexcommerce_catalog/product/", {"action": "publish", "_selected_action": [str(product.pk)]}
        )
        product.refresh_from_db()
        assert product.status == Product.STATUS_ACTIVE
