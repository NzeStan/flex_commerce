"""
Bulk set stock levels from CSV with columns ``sku,on_hand[,reorder_point]``::

    python manage.py import_inventory stock.csv [--dry-run]

Only tracked items (matched by SKU) are updated; each change is recorded as an
audited ``adjustment`` movement.
"""

import csv

from django.core.management.base import BaseCommand, CommandError

from flexcommerce_inventory.models import InventoryItem


class Command(BaseCommand):
    help = "Set on-hand stock from a CSV file with columns sku,on_hand[,reorder_point]."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, path, dry_run=False, **options):
        try:
            handle = open(path, newline="", encoding="utf-8-sig")  # noqa: SIM115
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        updated, missing, invalid = 0, [], []
        with handle:
            for row_number, row in enumerate(csv.DictReader(handle), start=2):
                sku = (row.get("sku") or "").strip()
                try:
                    on_hand = int(row.get("on_hand") or "")
                    reorder = row.get("reorder_point")
                    reorder = int(reorder) if reorder not in (None, "") else None
                    if on_hand < 0 or (reorder is not None and reorder < 0):
                        raise ValueError
                except ValueError:
                    invalid.append(row_number)
                    continue
                item = InventoryItem.objects.filter(sku=sku).first() if sku else None
                if item is None:
                    missing.append(sku)
                    continue
                if not dry_run:
                    item.adjust(on_hand, note="CSV import")
                    if reorder is not None:
                        InventoryItem.objects.filter(pk=item.pk).update(reorder_point=reorder)
                updated += 1
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}Updated {updated} items."))
        if missing:
            self.stdout.write(self.style.WARNING(f"Unknown SKUs: {', '.join(missing[:50])}"))
        if invalid:
            self.stdout.write(self.style.WARNING(f"Invalid rows: {invalid[:50]}"))
