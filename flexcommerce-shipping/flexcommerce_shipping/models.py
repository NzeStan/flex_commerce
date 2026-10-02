"""
FlexCommerce Shipping.

Zone-based shipping for Nigeria out of the box (all 36 states + FCT mapped to
geopolitical zones) and fully configurable for any country:

* ``ShippingZone``   – states / countries covered (explicit lists win over the
                       built-in Nigerian map)
* ``ShippingMethod`` – flat, per-item, weight tiers or free; free-shipping
                       threshold; pay-on-delivery availability; VAT on shipping
* ``PickupStation``  – Jumia-style pick-up points with optional fee override
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.conf import fc_setting
from flexcommerce_core.models import TimeStampedUUIDModel
from flexcommerce_core.utils.vat import ZERO, compute_tax, round_price, to_decimal


def normalise_region(value: str) -> str:
    value = (value or "").strip().lower().replace("-", " ").replace("_", " ")
    for suffix in (" state",):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return " ".join(value.split())


class ShippingZone(TimeStampedUUIDModel):
    ZONE_LAGOS = "lagos"
    ZONE_ABUJA = "abuja"
    ZONE_SOUTH_WEST = "south_west"
    ZONE_SOUTH_EAST = "south_east"
    ZONE_SOUTH_SOUTH = "south_south"
    ZONE_NORTH_CENTRAL = "north_central"
    ZONE_NORTH_WEST = "north_west"
    ZONE_NORTH_EAST = "north_east"
    ZONE_INTERNATIONAL = "international"
    ZONE_CHOICES = [
        (ZONE_LAGOS, _("Lagos")),
        (ZONE_ABUJA, _("Abuja (FCT)")),
        (ZONE_SOUTH_WEST, _("South West")),
        (ZONE_SOUTH_EAST, _("South East")),
        (ZONE_SOUTH_SOUTH, _("South South")),
        (ZONE_NORTH_CENTRAL, _("North Central")),
        (ZONE_NORTH_WEST, _("North West")),
        (ZONE_NORTH_EAST, _("North East")),
        (ZONE_INTERNATIONAL, _("International")),
    ]

    NIGERIAN_STATES = {
        ZONE_LAGOS: ["Lagos"],
        ZONE_ABUJA: ["FCT"],
        ZONE_SOUTH_WEST: ["Ogun", "Oyo", "Osun", "Ondo", "Ekiti"],
        ZONE_SOUTH_EAST: ["Abia", "Anambra", "Ebonyi", "Enugu", "Imo"],
        ZONE_SOUTH_SOUTH: ["Akwa Ibom", "Bayelsa", "Cross River", "Delta", "Edo", "Rivers"],
        ZONE_NORTH_CENTRAL: ["Benue", "Kogi", "Kwara", "Nasarawa", "Niger", "Plateau"],
        ZONE_NORTH_WEST: ["Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Sokoto", "Zamfara"],
        ZONE_NORTH_EAST: ["Adamawa", "Bauchi", "Borno", "Gombe", "Taraba", "Yobe"],
    }
    ALIASES = {
        "abuja": "fct",
        "federal capital territory": "fct",
        "nassarawa": "nasarawa",
        "akwa-ibom": "akwa ibom",
    }
    STATE_ZONE_MAP = {normalise_region(state): zone for zone, states in NIGERIAN_STATES.items() for state in states} | {
        "abuja": ZONE_ABUJA,
        "federal capital territory": ZONE_ABUJA,
        "nassarawa": ZONE_NORTH_CENTRAL,
    }

    name = models.CharField(_("name"), max_length=100)
    zone_code = models.SlugField(_("zone code"), max_length=50, unique=True)
    states = models.JSONField(
        _("states / regions"),
        default=list,
        blank=True,
        help_text=_("State names covered. Overrides the built-in Nigerian mapping."),
    )
    countries = models.JSONField(
        _("countries"),
        default=list,
        blank=True,
        help_text=_('Country names or ISO codes, e.g. ["Ghana", "GH"]. Leave empty for the home country.'),
    )
    is_active = models.BooleanField(_("is active"), default=True)
    sort_order = models.PositiveSmallIntegerField(_("sort order"), default=0)

    class Meta:
        verbose_name = _("shipping zone")
        verbose_name_plural = _("shipping zones")
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.zone_code})"

    @classmethod
    def home_country(cls):
        return normalise_region(fc_setting("SHIPPING_HOME_COUNTRY", "Nigeria"))

    @classmethod
    def get_zone_for_address(cls, state: str = "", country: str = "") -> "ShippingZone | None":
        """Resolve a delivery address to a zone, or ``None`` if not served."""
        state_n = normalise_region(state)
        state_n = cls.ALIASES.get(state_n, state_n)
        country_n = normalise_region(country) or cls.home_country()
        zones = list(cls.objects.filter(is_active=True))
        is_home = country_n in (cls.home_country(), "ng")
        if not is_home:
            for zone in zones:
                if country_n in {normalise_region(c) for c in zone.countries}:
                    return zone
            return None
        for zone in zones:
            states = {cls.ALIASES.get(normalise_region(s), normalise_region(s)) for s in zone.states}
            if state_n and state_n in states and not zone.countries:
                return zone
        code = cls.STATE_ZONE_MAP.get(state_n)
        if code:
            return next((z for z in zones if z.zone_code == code and not z.states), None)
        return None

    @classmethod
    def get_zone_for_state(cls, state_name: str) -> "ShippingZone | None":
        """Return the zone for ``state``, or ``None`` for unknown states."""
        return cls.get_zone_for_address(state=state_name)


class ShippingMethod(TimeStampedUUIDModel):
    RATE_FLAT = "flat"
    RATE_PER_ITEM = "per_item"
    RATE_WEIGHT = "weight"
    RATE_FREE = "free"
    RATE_CHOICES = [
        (RATE_FLAT, _("Flat Rate")),
        (RATE_PER_ITEM, _("Per Item")),
        (RATE_WEIGHT, _("Weight Based")),
        (RATE_FREE, _("Free Shipping")),
    ]

    name = models.CharField(_("name"), max_length=200)
    code = models.SlugField(_("code"), max_length=50, blank=True)
    carrier = models.CharField(_("carrier"), max_length=100, blank=True)
    description = models.CharField(_("description"), max_length=255, blank=True)
    zones = models.ManyToManyField(ShippingZone, related_name="methods", blank=True)

    rate_type = models.CharField(_("rate type"), max_length=20, choices=RATE_CHOICES, default=RATE_FLAT)
    base_rate = models.DecimalField(_("base rate"), max_digits=14, decimal_places=2, default=Decimal("0.00"))
    per_item_rate = models.DecimalField(_("per item rate"), max_digits=14, decimal_places=2, default=Decimal("0.00"))
    weight_tiers = models.JSONField(
        _("weight tiers"),
        default=list,
        blank=True,
        help_text=_('[{"max_kg": 1, "rate": 1500}, {"max_kg": 5, "rate": 3000}] — cheapest tier that fits.'),
    )
    per_kg_rate = models.DecimalField(
        _("rate per extra kg"),
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Charged per kg above the heaviest tier (or per kg if there are no tiers)."),
    )
    max_weight = models.DecimalField(_("max weight (kg)"), max_digits=10, decimal_places=3, null=True, blank=True)
    free_shipping_threshold = models.DecimalField(
        _("free shipping from"), max_digits=14, decimal_places=2, null=True, blank=True
    )
    is_pickup = models.BooleanField(_("is pickup"), default=False)
    pay_on_delivery = models.BooleanField(_("pay on delivery available"), default=True)
    estimated_days_min = models.PositiveSmallIntegerField(_("min delivery days"), default=1)
    estimated_days_max = models.PositiveSmallIntegerField(_("max delivery days"), default=5)
    apply_vat = models.BooleanField(_("apply VAT to shipping"), default=False)
    is_active = models.BooleanField(_("is active"), default=True)
    sort_order = models.PositiveSmallIntegerField(_("sort order"), default=0)

    class Meta:
        verbose_name = _("shipping method")
        verbose_name_plural = _("shipping methods")
        ordering = ["sort_order", "base_rate"]

    def __str__(self):
        return f"{self.name} ({self.rate_type})"

    def clean(self):
        for tier in self.weight_tiers or []:
            if not isinstance(tier, dict) or "max_kg" not in tier or "rate" not in tier:
                raise ValidationError({"weight_tiers": _('Each tier needs "max_kg" and "rate".')})
        if self.estimated_days_min > self.estimated_days_max:
            raise ValidationError({"estimated_days_max": _("Must be ≥ the minimum.")})

    def supports_weight(self, weight) -> bool:
        return self.max_weight is None or to_decimal(weight) <= self.max_weight

    def _weight_cost(self, weight: Decimal) -> Decimal:
        tiers = sorted(
            ({"max_kg": to_decimal(t["max_kg"]), "rate": to_decimal(t["rate"])} for t in self.weight_tiers or []),
            key=lambda t: t["max_kg"],
        )
        for tier in tiers:
            if weight <= tier["max_kg"]:
                return self.base_rate + tier["rate"]
        heaviest = tiers[-1] if tiers else {"max_kg": ZERO, "rate": ZERO}
        extra_kg = max(ZERO, weight - heaviest["max_kg"]).to_integral_value(rounding="ROUND_CEILING")
        return self.base_rate + heaviest["rate"] + self.per_kg_rate * extra_kg

    def calculate_cost(self, cart_total=Decimal("0"), item_count: int = 0, weight=Decimal("0")) -> Decimal:
        """Shipping cost for a cart context (before VAT)."""
        cart_total = to_decimal(cart_total)
        if self.rate_type == self.RATE_FREE:
            return ZERO
        if self.free_shipping_threshold is not None and cart_total >= self.free_shipping_threshold:
            return ZERO
        if self.rate_type == self.RATE_PER_ITEM:
            return round_price(self.base_rate + self.per_item_rate * int(item_count))
        if self.rate_type == self.RATE_WEIGHT:
            return round_price(self._weight_cost(to_decimal(weight)))
        return round_price(self.base_rate)

    def get_cost_with_vat(self, cart_total=Decimal("0"), item_count: int = 0, weight=Decimal("0")) -> dict:
        cost = self.calculate_cost(cart_total, item_count, weight)
        if self.apply_vat and fc_setting("VAT_ON_SHIPPING", False):
            return compute_tax(cost, inclusive=False)
        return {"net": cost, "vat": ZERO, "gross": cost, "rate": ZERO}

    def estimated_delivery(self, start=None):
        """``(earliest, latest)`` dates, skipping Sundays."""
        start = (start or timezone.localtime()).date()

        def add_days(days):
            day = start
            while days > 0:
                day += timedelta(days=1)
                if day.weekday() != 6:
                    days -= 1
            return day

        return add_days(self.estimated_days_min), add_days(self.estimated_days_max)


class PickupStation(TimeStampedUUIDModel):
    name = models.CharField(_("name"), max_length=200)
    code = models.SlugField(_("code"), max_length=50, unique=True)
    zone = models.ForeignKey(ShippingZone, on_delete=models.PROTECT, related_name="pickup_stations")
    state = models.CharField(_("state"), max_length=100)
    city = models.CharField(_("city"), max_length=100)
    address = models.CharField(_("address"), max_length=500)
    landmark = models.CharField(_("landmark"), max_length=255, blank=True)
    phone = models.CharField(_("phone"), max_length=20, blank=True)
    opening_hours = models.CharField(_("opening hours"), max_length=200, blank=True)
    fee = models.DecimalField(
        _("pickup fee"),
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Overrides the pickup method's rate for this station."),
    )
    latitude = models.DecimalField(_("latitude"), max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(_("longitude"), max_digits=9, decimal_places=6, null=True, blank=True)
    is_active = models.BooleanField(_("is active"), default=True)

    class Meta:
        verbose_name = _("pickup station")
        verbose_name_plural = _("pickup stations")
        ordering = ["state", "city", "name"]
        indexes = [models.Index(fields=["state", "is_active"], name="ship_station_state_idx")]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state})"

    def to_snapshot(self):
        return {
            "id": str(self.pk),
            "code": self.code,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "landmark": self.landmark,
            "phone": self.phone,
            "opening_hours": self.opening_hours,
        }
