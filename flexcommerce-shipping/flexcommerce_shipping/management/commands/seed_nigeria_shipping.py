"""
Create the Nigerian geopolitical shipping zones and (optionally) starter methods::

    python manage.py seed_nigeria_shipping [--with-methods]

Safe to run repeatedly: existing zones are left untouched.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from flexcommerce_shipping.models import ShippingMethod, ShippingZone

STARTER_RATES = {
    ShippingZone.ZONE_LAGOS: (Decimal("1500"), 1, 2),
    ShippingZone.ZONE_SOUTH_WEST: (Decimal("2500"), 2, 4),
    ShippingZone.ZONE_ABUJA: (Decimal("3000"), 2, 4),
    ShippingZone.ZONE_SOUTH_EAST: (Decimal("3000"), 3, 5),
    ShippingZone.ZONE_SOUTH_SOUTH: (Decimal("3000"), 3, 5),
    ShippingZone.ZONE_NORTH_CENTRAL: (Decimal("3500"), 3, 6),
    ShippingZone.ZONE_NORTH_WEST: (Decimal("4000"), 4, 7),
    ShippingZone.ZONE_NORTH_EAST: (Decimal("4500"), 4, 8),
}


class Command(BaseCommand):
    help = "Seed Nigerian shipping zones (and starter door-delivery methods with --with-methods)."

    def add_arguments(self, parser):
        parser.add_argument("--with-methods", action="store_true")

    def handle(self, *args, with_methods=False, **options):
        labels = dict(ShippingZone.ZONE_CHOICES)
        created = 0
        for index, code in enumerate(ShippingZone.NIGERIAN_STATES):
            zone, was_created = ShippingZone.objects.get_or_create(
                zone_code=code, defaults={"name": str(labels[code]), "sort_order": index}
            )
            created += was_created
            if with_methods and not zone.methods.exists():
                rate, low, high = STARTER_RATES[code]
                method = ShippingMethod.objects.create(
                    name=f"Door delivery ({zone.name})",
                    code=f"door-{code}",
                    rate_type=ShippingMethod.RATE_FLAT,
                    base_rate=rate,
                    estimated_days_min=low,
                    estimated_days_max=high,
                    sort_order=index,
                )
                method.zones.add(zone)
        self.stdout.write(self.style.SUCCESS(f"Created {created} zones."))
