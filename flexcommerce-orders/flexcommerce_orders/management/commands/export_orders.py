"""Export orders to CSV (streams rows; safe for large tables)."""

import csv

from django.core.management.base import BaseCommand

from flexcommerce_core.utils.csv import safe_row


class Command(BaseCommand):
    help = "Export orders to CSV."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="orders_export.csv", help="Output file path")
        parser.add_argument("--status", default=None, help="Filter by status")
        parser.add_argument("--from-date", default=None, help="From date YYYY-MM-DD")
        parser.add_argument("--to-date", default=None, help="To date YYYY-MM-DD")

    def handle(self, *args, **options):
        from django.db.models import Count, Sum

        from flexcommerce_orders.models import Order

        qs = Order.objects.select_related("user").annotate(units=Sum("items__quantity"), lines=Count("items"))
        if options["status"]:
            qs = qs.filter(status=options["status"])
        if options["from_date"]:
            qs = qs.filter(created_at__date__gte=options["from_date"])
        if options["to_date"]:
            qs = qs.filter(created_at__date__lte=options["to_date"])

        count = 0
        with open(options["output"], "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "order_number",
                    "email",
                    "phone",
                    "status",
                    "payment_status",
                    "payment_method",
                    "subtotal",
                    "discount",
                    "shipping",
                    "tax",
                    "grand_total",
                    "amount_paid",
                    "amount_refunded",
                    "currency",
                    "coupon_code",
                    "item_count",
                    "state",
                    "created_at",
                ]
            )
            for order in qs.order_by("created_at").iterator(chunk_size=2000):
                writer.writerow(
                    safe_row(
                        [
                            order.order_number,
                            order.customer_email,
                            order.phone,
                            order.status,
                            order.payment_status,
                            order.payment_method,
                            order.subtotal,
                            order.discount_amount,
                            order.shipping_cost,
                            order.tax_total,
                            order.grand_total,
                            order.amount_paid,
                            order.amount_refunded,
                            order.currency,
                            order.coupon_code,
                            order.units or 0,
                            (order.shipping_address or {}).get("state", ""),
                            order.created_at.isoformat(),
                        ]
                    )
                )
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Exported {count} orders to {options['output']}"))
