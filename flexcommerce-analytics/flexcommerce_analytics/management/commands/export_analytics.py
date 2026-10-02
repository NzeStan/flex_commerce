"""Export any analytics report to CSV (formula-injection safe)."""

import csv
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from flexcommerce_analytics import reports
from flexcommerce_core.utils.csv import safe_dict


class Command(BaseCommand):
    help = "Export an analytics report to CSV."

    def add_arguments(self, parser):
        parser.add_argument("--report", choices=list(reports.REPORTS), default="revenue")
        parser.add_argument("--start-date", default=None)
        parser.add_argument("--end-date", default=None)
        parser.add_argument("--output", default=None)
        parser.add_argument("--period", choices=list(reports.PERIODS), default="daily")
        parser.add_argument("--by", choices=["revenue", "quantity"], default="revenue")
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        today = timezone.localdate()
        try:
            start = date.fromisoformat(options["start_date"]) if options["start_date"] else today - timedelta(days=30)
            end = date.fromisoformat(options["end_date"]) if options["end_date"] else today
        except ValueError as exc:
            raise CommandError(f"Invalid date: {exc}") from exc
        name = options["report"]
        if name == "revenue":
            rows = reports.revenue_report(start, end, period=options["period"])
        elif name == "top_products":
            rows = reports.top_products_report(start, end, limit=options["limit"], by=options["by"])
        else:
            rows = reports.REPORTS[name](start, end)
        if not rows:
            self.stdout.write(self.style.WARNING("No data found for the selected date range."))
            return
        output = options["output"] or f"fc_{name}_{start}_{end}.csv"
        with open(output, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(safe_dict(r) for r in rows)
        self.stdout.write(self.style.SUCCESS(f"Exported {len(rows)} rows to {output}"))
