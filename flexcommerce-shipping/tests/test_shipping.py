from datetime import datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from flexcommerce_core.exceptions import ShippingMethodNotAvailableError
from flexcommerce_shipping import services
from flexcommerce_shipping.models import (
    PickupStation,
    ShippingMethod,
    ShippingZone,
    normalise_region,
)

pytestmark = pytest.mark.django_db
D = Decimal


class TestZones:
    @pytest.mark.parametrize(
        "state,zone",
        [
            ("Lagos", "lagos"),
            ("lagos state", "lagos"),
            ("Abuja", "abuja"),
            ("FCT", "abuja"),
            ("Federal Capital Territory", "abuja"),
            ("Nassarawa", "north_central"),
            ("Nasarawa", "north_central"),
            ("Akwa-Ibom", "south_south"),
            ("cross river", "south_south"),
            ("Kano", "north_west"),
            ("Borno", "north_east"),
            ("Imo", "south_east"),
            ("Ekiti", "south_west"),
        ],
    )
    def test_builtin_map(self, state, zone):
        assert (
            ShippingZone.STATE_ZONE_MAP[ShippingZone.ALIASES.get(normalise_region(state), normalise_region(state))]
            == zone
        )

    def test_all_37_states_mapped(self):
        assert len(services.nigerian_states()) == 37

    def test_unknown_state_is_not_served(self, lagos):
        """Unknown states return no zone rather than silently falling back to another zone."""
        ShippingZone.objects.create(name="NE", zone_code="north_east")
        assert ShippingZone.get_zone_for_state("Atlantis") is None
        assert ShippingZone.get_zone_for_state("Lagos") == lagos

    def test_inactive_zone(self, lagos):
        lagos.is_active = False
        lagos.save()
        assert ShippingZone.get_zone_for_address("Lagos") is None

    def test_custom_state_lists_override(self, lagos):
        mainland = ShippingZone.objects.create(name="Lagos Mainland", zone_code="lagos-mainland", states=["Lagos"])
        assert ShippingZone.get_zone_for_address("Lagos") == mainland

    def test_international(self):
        ghana = ShippingZone.objects.create(name="Ghana", zone_code="ghana", countries=["Ghana", "GH"])
        assert ShippingZone.get_zone_for_address("Accra", "Ghana") == ghana
        assert ShippingZone.get_zone_for_address("", "gh") == ghana
        assert ShippingZone.get_zone_for_address("Nairobi", "Kenya") is None
        assert ShippingZone.get_zone_for_address("Lagos", "NG") is None  # no Lagos zone seeded here
        assert str(ghana) == "Ghana (ghana)"

    def test_home_country_setting(self, fc, lagos):
        fc(SHIPPING_HOME_COUNTRY="Ghana")
        assert ShippingZone.get_zone_for_address("Lagos", "Nigeria") is None


class TestMethods:
    def test_flat_and_threshold(self, standard):
        assert standard.calculate_cost(D("1000")) == D("2000.00")
        assert standard.calculate_cost(D("50000")) == D("0.00")
        assert "Standard" in str(standard)

    def test_per_item_and_free(self):
        per_item = ShippingMethod(
            name="p",
            rate_type=ShippingMethod.RATE_PER_ITEM,
            base_rate=D("500"),
            per_item_rate=D("200"),
        )
        assert per_item.calculate_cost(item_count=3) == D("1100.00")
        assert ShippingMethod(name="f", rate_type=ShippingMethod.RATE_FREE, base_rate=D("9")).calculate_cost() == D(
            "0.00"
        )

    def test_weight_tiers(self):
        m = ShippingMethod(
            name="w",
            rate_type=ShippingMethod.RATE_WEIGHT,
            base_rate=D("100"),
            weight_tiers=[{"max_kg": 5, "rate": 3000}, {"max_kg": 1, "rate": 1500}],
            per_kg_rate=D("400"),
        )
        assert m.calculate_cost(weight=D("0.5")) == D("1600.00")
        assert m.calculate_cost(weight=D("4")) == D("3100.00")
        assert m.calculate_cost(weight=D("7.2")) == D("4300.00")  # 3000 + 3 extra kg (ceil) * 400 + 100
        no_tiers = ShippingMethod(name="n", rate_type=ShippingMethod.RATE_WEIGHT, per_kg_rate=D("250"))
        assert no_tiers.calculate_cost(weight=D("2.1")) == D("750.00")

    def test_vat_on_shipping(self, standard, fc):
        standard.apply_vat = True
        assert standard.get_cost_with_vat(D("0"))["vat"] == D("0.00")
        fc(VAT_ON_SHIPPING=True)
        info = standard.get_cost_with_vat(D("0"))
        assert (info["net"], info["vat"], info["gross"]) == (
            D("2000.00"),
            D("150.00"),
            D("2150.00"),
        )

    def test_clean(self):
        with pytest.raises(ValidationError):
            ShippingMethod(name="x", weight_tiers=[{"rate": 1}]).clean()
        with pytest.raises(ValidationError):
            ShippingMethod(name="x", estimated_days_min=5, estimated_days_max=2).clean()

    def test_estimated_delivery_skips_sundays(self):
        m = ShippingMethod(name="x", estimated_days_min=1, estimated_days_max=2)
        saturday = timezone.make_aware(datetime(2026, 10, 3, 12))
        earliest, latest = m.estimated_delivery(saturday)
        assert earliest.isoformat() == "2026-10-05" and latest.isoformat() == "2026-10-06"

    def test_max_weight(self, standard):
        standard.max_weight = D("10")
        assert standard.supports_weight(D("9")) and not standard.supports_weight(D("11"))


class TestQuotes:
    def test_quote_all(self, standard, pickup):
        zone, quotes = services.quote_all(state="Lagos", cart_total=D("1000"), item_count=2)
        assert zone.zone_code == "lagos"
        assert [q.method.name for q in quotes] == ["Standard", "Pickup"]
        assert quotes[0].to_dict()["cost"] == "2000.00"
        assert services.quote_all(state="Nowhere") == (None, [])

    def test_free_shipping_coupon(self, standard):
        _, quotes = services.quote_all(state="Rivers", free_shipping=True)
        assert quotes[0].gross == D("0.00") and quotes[0].free_shipping_applied

    def test_quote_method_validates_zone(self, standard, pickup, station):
        q = services.quote_method(standard.pk, state="Rivers", cart_total=D("1000"))
        assert q.gross == D("2000.00")
        with pytest.raises(ShippingMethodNotAvailableError):
            services.quote_method(pickup.pk, state="Rivers")  # pickup only in Lagos
        with pytest.raises(ShippingMethodNotAvailableError):
            services.quote_method(standard.pk, state="Atlantis")
        with pytest.raises(ShippingMethodNotAvailableError):
            services.quote_method("00000000-0000-0000-0000-000000000000", state="Lagos")

    def test_pickup_station(self, pickup, station):
        with pytest.raises(ShippingMethodNotAvailableError) as exc:
            services.quote_method(pickup.pk, state="Lagos")
        assert exc.value.code == "pickup_station_required"
        with pytest.raises(ShippingMethodNotAvailableError):
            services.quote_method(pickup.pk, state="Lagos", pickup_station_id="00000000-0000-0000-0000-000000000000")
        q = services.quote_method(pickup.pk, state="Lagos", pickup_station_id=station.pk)
        assert q.gross == D("800.00") and q.to_dict()["pickup_station"]["code"] == "ikeja"
        station.fee = D("500")
        station.save()
        assert services.quote_method(pickup.pk, state="Lagos", pickup_station_id=station.pk).gross == D("500.00")
        assert "Ikeja Hub" in str(station)

    def test_overweight_excluded(self, standard):
        standard.max_weight = D("5")
        standard.save()
        assert services.quote_all(state="Lagos", weight=D("6"))[1] == []
        with pytest.raises(ShippingMethodNotAvailableError):
            services.quote_method(standard.pk, state="Lagos", weight=D("6"))


class TestAPI:
    def test_calculate(self, client, standard):
        data = client.post("/api/shipping/methods/calculate/", {"state": "Lagos", "cart_total": "1000"}).json()
        assert data["deliverable"] and data["zone"] == "lagos" and data["options"][0]["cost"] == "2000.00"
        nowhere = client.post("/api/shipping/methods/calculate/", {"state": "Mars"}).json()
        assert nowhere == {"zone": None, "deliverable": False, "options": []}
        assert client.post("/api/shipping/methods/calculate/", {"cart_total": "-1"}).status_code == 400

    def test_states(self, client):
        states = client.get("/api/shipping/states/").json()
        assert {"name": "Rivers", "zone": "south_south"} in states

    def test_pickup_stations(self, client, station, lagos):
        PickupStation.objects.create(
            name="Closed",
            code="closed",
            zone=lagos,
            state="Lagos",
            city="Lekki",
            address="x",
            is_active=False,
        )
        assert [s["code"] for s in client.get("/api/shipping/pickup-stations/?state=lagos").json()["results"]] == [
            "ikeja"
        ]
        assert client.get("/api/shipping/pickup-stations/?city=Lekki").json()["results"] == []
        assert client.post("/api/shipping/pickup-stations/", {}).status_code in (401, 403)

    def test_staff_crud(self, staff_client, lagos):
        resp = staff_client.post(
            "/api/shipping/methods/",
            {
                "name": "Express",
                "base_rate": "5000",
                "zones": [str(lagos.pk)],
                "weight_tiers": [{"max_kg": 1, "rate": 1}],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["estimated_delivery"] == "1–5 days"
        bad = staff_client.post("/api/shipping/methods/", {"name": "Bad", "weight_tiers": [{"x": 1}]}, format="json")
        assert bad.status_code == 400
        patch = staff_client.patch(
            f"/api/shipping/methods/{resp.data['id']}/", {"estimated_days_min": 9}, format="json"
        )
        assert patch.status_code == 400
        same = staff_client.patch(f"/api/shipping/methods/{resp.data['id']}/", {"estimated_days_min": 5}, format="json")
        assert same.data["estimated_delivery"] == "5 day(s)"
        assert staff_client.get("/api/shipping/zones/").status_code == 200

    def test_seed_command_and_admin(self, client):
        from django.contrib.auth import get_user_model

        out = StringIO()
        call_command("seed_nigeria_shipping", "--with-methods", stdout=out)
        call_command("seed_nigeria_shipping", "--with-methods", stdout=out)
        assert ShippingZone.objects.count() == 8 and ShippingMethod.objects.count() == 8
        zone, quotes = services.quote_all(state="Borno")
        assert zone.zone_code == "north_east" and quotes[0].gross == D("4500.00")
        call_command("makemigrations", "flexcommerce_shipping", "--check", "--dry-run", stdout=StringIO())
        client.force_login(get_user_model().objects.create_superuser("r", "r@x.com", "x"))
        for m in ("shippingzone", "shippingmethod", "pickupstation"):
            assert client.get(f"/admin/flexcommerce_shipping/{m}/").status_code == 200
