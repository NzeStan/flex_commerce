"""
Build or refresh DailyRevenueSummary rows (also run hourly by ``flexcommerce_run_jobs``).

    python manage.py aggregate_analytics                         # yesterday
    python manage.py aggregate_analytics --start-date 2024-01-01 --end-date 2024-12-31 --rebuild
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from flexcommerce_analytics.aggregation import aggregate_range


class Command(BaseCommand):
    help = "Aggregate daily revenue summaries."

    def add_arguments(self, parser):
        parser.add_argument("--start-date", default=None)
        parser.add_argument("--end-date", default=None)
        parser.add_argument("--rebuild", action="store_true", help="Overwrite existing summaries in the range.")

    def handle(self, *args, **options):
        yesterday = timezone.localdate() - timedelta(days=1)
        try:
            start = date.fromisoformat(options["start_date"]) if options["start_date"] else yesterday
            end = date.fromisoformat(options["end_date"]) if options["end_date"] else start
        except ValueError as exc:
            raise CommandError(f"Invalid date: {exc}") from exc
        if start > end:
            raise CommandError("--start-date must be before --end-date")
        result = aggregate_range(start, end, rebuild=options["rebuild"])
        self.stdout.write(
            self.style.SUCCESS(f"Done. Created: {result['created']}, Updated: {result['updated']} daily summaries.")
        )
