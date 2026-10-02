from decimal import Decimal
from io import StringIO
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

pytest_plugins = ["flexcommerce_core.testing"]

ADDRESS = {
    "first_name": "Adaeze",
    "last_name": "Okonkwo",
    "phone": "08031234567",
    "line1": "12 Admiralty Way",
    "city": "Lekki",
    "lga": "Eti-Osa",
    "state": "Lagos",
    "country": "Nigeria",
    "landmark": "Near Lekki Phase 1 gate",
}


@pytest.fixture
def user_model():
    return get_user_model()


@pytest.fixture
def staff(db, user_model):
    return user_model.objects.create_user("admin", "ops@naijamart.example", "pass", is_staff=True, is_superuser=True)


@pytest.fixture
def customer(db, user_model):
    return user_model.objects.create_user(
        "adaeze", "adaeze@example.com", "pass", first_name="Adaeze", last_name="Okonkwo", phone="08031234567"
    )


@pytest.fixture
def seller(db, user_model):
    return user_model.objects.create_user("seller", "seller@abashoes.example", "pass")


def api(user=None, **headers):
    c = APIClient(**headers)
    if user is not None:
        c.force_authenticate(user)
    return c


@pytest.fixture
def shipping(db):
    call_command("seed_nigeria_shipping", "--with-methods", stdout=StringIO())
    from flexcommerce_shipping.models import PickupStation, ShippingMethod, ShippingZone

    lagos = ShippingZone.objects.get(zone_code="lagos")
    pickup = ShippingMethod.objects.create(
        name="Pickup station", base_rate=Decimal("800"), is_pickup=True, pay_on_delivery=True, sort_order=0
    )
    pickup.zones.add(lagos)
    station = PickupStation.objects.create(
        name="Lekki Hub", code="lekki", zone=lagos, state="Lagos", city="Lekki", address="Admiralty Way"
    )
    return {"door": ShippingMethod.objects.get(code="door-lagos"), "pickup": pickup, "station": station}


@pytest.fixture
def catalog(db, staff):
    """Two products: one sold by the store, one by a marketplace vendor."""
    from flexcommerce_catalog.models import Brand, Category, Product, ProductImage

    phones = Category.objects.create(name="Phones")
    brand = Brand.objects.create(name="Tecno")
    s = api(staff)
    resp = s.post(
        "/api/catalog/products/",
        {
            "name": "Tecno Camon 30",
            "status": "active",
            "brand": str(brand.pk),
            "categories": [str(phones.pk)],
            "variants": [
                {"sku": "CAMON30-BLK", "price": "250000", "attributes": {"color": "Black"}},
                {"sku": "CAMON30-WHT", "price": "255000", "attributes": {"color": "White"}},
            ],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    phone = Product.objects.get(slug="tecno-camon-30")
    ProductImage.objects.create(product=phone, url="https://cdn.naijamart.example/camon.jpg", is_primary=True)
    for variant in phone.variants.all():
        r = s.post("/api/inventory/", {"product_id": str(variant.pk), "on_hand": 10, "reorder_point": 2}, format="json")
        assert r.status_code == 201, r.json()
    return {
        "phone": phone,
        "black": phone.variants.get(sku="CAMON30-BLK"),
        "white": phone.variants.get(sku="CAMON30-WHT"),
        "category": phones,
        "brand": brand,
    }


@pytest.fixture
def vendor_product(db, seller, staff):
    from flexcommerce_catalog.models import Product
    from flexcommerce_marketplace.models import Vendor

    s = api(seller)
    assert (
        s.post(
            "/api/vendors/apply/",
            {
                "name": "Aba Shoes",
                "account_number": "0123456789",
                "bank_name": "GTBank",
                "account_name": "Aba Shoes Ltd",
            },
            format="json",
        ).status_code
        == 201
    )
    vendor = Vendor.objects.get(name="Aba Shoes")
    assert api(staff).post(f"/api/vendors/{vendor.slug}/approve/").status_code == 200
    resp = s.post(
        "/api/catalog/products/",
        {"name": "Leather Loafers", "status": "active", "price": "30000", "sku": "ABA-LOAFER-42"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    product = Product.objects.get(slug="leather-loafers")
    variant = product.variants.get()
    assert (
        api(staff).post("/api/inventory/", {"product_id": str(variant.pk), "on_hand": 5}, format="json").status_code
        == 201
    )
    return {"vendor": vendor, "product": product, "variant": variant}


@pytest.fixture
def paystack():
    """Mock Paystack's API. ``paystack.charge(amount_kobo)`` sets the verify response."""
    state = {"verify_amount": None}

    def fake(method, url, headers=None, payload=None, timeout=None):
        if url.endswith("/transaction/initialize"):
            return 200, {
                "status": True,
                "data": {
                    "authorization_url": "https://checkout.paystack.com/xyz",
                    "access_code": "ac",
                    "reference": payload["reference"],
                },
            }
        if "/transaction/verify/" in url:
            return 200, {
                "status": True,
                "data": {
                    "id": 777,
                    "status": "success",
                    "amount": state["verify_amount"],
                    "currency": "NGN",
                    "channel": "card",
                },
            }
        if url.endswith("/refund"):
            return 200, {"status": True, "data": {"id": 9001}}
        raise AssertionError(url)

    with mock.patch("flexcommerce_payments.gateways.paystack.request_json", side_effect=fake) as patched:
        patched.state = state
        yield patched


@pytest.fixture
def webhook_sink(db):
    from flexcommerce_core import webhooks
    from flexcommerce_core.models import WebhookEndpoint

    WebhookEndpoint.objects.create(url="https://erp.naijamart.example/hooks", event="*", secret="whsec")
    with mock.patch.object(webhooks, "_post", return_value=(200, "ok")) as post:
        yield post
