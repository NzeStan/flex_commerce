import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytest_plugins = ["flexcommerce_core.testing"]

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", email="test@example.com", password="pass1234")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="other", email="other@example.com", password="pass1234")


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(username="admin", email="admin@example.com", password="pass1234")


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client
