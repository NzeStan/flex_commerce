from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_catalog.models import Brand, Category, Product, ProductImage, ProductVariant

pytest_plugins = ["flexcommerce_core.testing"]

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="shopper", email="s@example.com", password="x")


@pytest.fixture
def staff(db):
    return User.objects.create_user(username="staff", email="staff@example.com", password="x", is_staff=True)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def staff_client(staff):
    c = APIClient()
    c.force_authenticate(staff)
    return c


@pytest.fixture
def auth_client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def phones(db):
    electronics = Category.objects.create(name="Electronics")
    return Category.objects.create(name="Phones", parent=electronics)


@pytest.fixture
def brand(db):
    return Brand.objects.create(name="Tecno")


def make_product(name="Camon 20", price="150000", brand=None, categories=(), status=Product.STATUS_ACTIVE, **kw):
    product = Product.objects.create(name=name, brand=brand, status=status, **kw)
    product.categories.set(categories)
    ProductVariant.objects.create(
        product=product, sku=f"SKU-{product.pk.hex[:8]}", price=Decimal(price), is_default=True
    )
    return product


@pytest.fixture
def product(phones, brand):
    p = make_product(brand=brand, categories=[phones], description="Great camera phone")
    ProductImage.objects.create(product=p, url="https://cdn.example.com/p.jpg", is_primary=True)
    return p
