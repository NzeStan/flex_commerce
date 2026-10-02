"""Release expired reservations and report items at or below their low-stock threshold."""

from django.core.management.base import BaseCommand

from flexcommerce_inventory.models import InventoryItem
from flexcommerce_inventory.services import release_expired


class Command(BaseCommand):
    help = "Release expired stock reservations and list low-stock items."

    def handle(self, *args, **options):
        result = release_expired(limit=100_000)
        self.stdout.write(self.style.SUCCESS(f"Released {result['released']} expired reservations."))
        low = [item for item in InventoryItem.objects.order_by("sku").iterator() if item.is_low]
        if not low:
            self.stdout.write("All inventory levels healthy.")
            return
        self.stdout.write(self.style.WARNING(f"{len(low)} items at or below their low-stock threshold:"))
        for item in low:
            self.stdout.write(
                f"  - SKU: {item.sku or item.object_id}, available: {item.available}, "
                f"threshold: {item.low_stock_threshold}"
            )
