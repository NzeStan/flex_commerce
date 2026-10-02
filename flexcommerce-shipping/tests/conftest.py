from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from flexcommerce_shipping.models import PickupStation, ShippingMethod, ShippingZone

pytest_plugins = ["flexcommerce_core.testing"]


@pytest.fixture
def lagos(db):
    return ShippingZone.objects.create(name="Lagos", zone_code=ShippingZone.ZONE_LAGOS)


@pytest.fixture
def south_south(db):
    return ShippingZone.objects.create(name="South South", zone_code=ShippingZone.ZONE_SOUTH_SOUTH)


@pytest.fixture
def standard(lagos, south_south):
    m = ShippingMethod.objects.create(
        name="Standard", base_rate=Decimal("2000"), free_shipping_threshold=Decimal("50000")
    )
    m.zones.add(lagos, south_south)
    return m


@pytest.fixture
def pickup(lagos):
    m = ShippingMethod.objects.create(name="Pickup", base_rate=Decimal("800"), is_pickup=True, sort_order=5)
    m.zones.add(lagos)
    return m


@pytest.fixture
def station(lagos):
    return PickupStation.objects.create(
        name="Ikeja Hub",
        code="ikeja",
        zone=lagos,
        state="Lagos",
        city="Ikeja",
        address="1 Obafemi Awolowo Way",
    )


@pytest.fixture
def staff_client(db):
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("s", "s@x.com", "x", is_staff=True))
    return c
