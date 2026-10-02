import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]


@pytest.fixture(autouse=True)
def _settings(fc):
    fc(PRODUCT_MODELS=["tests.Product"], THROTTLING_ENABLED=False)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        "amaka", "amaka@example.com", "pass", first_name="Amaka", last_name="Obi"
    )


@pytest.fixture
def other(db):
    return get_user_model().objects.create_user("emeka", "emeka@example.com", "pass")


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("staff", "s@example.com", "pass", is_staff=True)


@pytest.fixture
def product(db):
    return Product.objects.create(name="Ankara Dress")


def client_for(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user)
    return c
