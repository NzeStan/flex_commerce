import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_inventory.models import InventoryItem
from tests.models import Product

pytest_plugins = ["flexcommerce_core.testing"]


@pytest.fixture
def product(db):
    return Product.objects.create(name="Rice 50kg", sku="RICE-50")


@pytest.fixture
def item(product):
    inv = InventoryItem.get_for_product(product)
    inv.restock(10)
    inv.refresh_from_db()
    return inv


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("u", "u@example.com", "x")


@pytest.fixture
def staff_client(db):
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("staff", "s@example.com", "x", is_staff=True))
    return c


@pytest.fixture
def signals_log():
    """Capture every inventory signal sent during a test."""
    from flexcommerce_inventory import signals

    log = []
    names = ["low_stock", "out_of_stock", "restocked", "back_in_stock", "stock_changed"]
    receivers = {}
    for name in names:

        def receiver(sender, _name=name, **kwargs):
            log.append((_name, kwargs))

        receivers[name] = receiver
        getattr(signals, name).connect(receiver, weak=False, dispatch_uid=f"test-{name}")
    yield log
    for name in names:
        getattr(signals, name).disconnect(dispatch_uid=f"test-{name}")
