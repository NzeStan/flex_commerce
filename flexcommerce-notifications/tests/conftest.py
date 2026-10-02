import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytest_plugins = ["flexcommerce_core.testing"]


@pytest.fixture(autouse=True)
def _settings(fc):
    fc(STORE_NAME="Naija Mart", FRONTEND_URL="https://shop.example.com")


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        "chidi", "chidi@example.com", "x", first_name="Chidi", last_name="Okafor"
    )


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("staff", "staff@example.com", "x", is_staff=True)


def client_for(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user)
    return c
