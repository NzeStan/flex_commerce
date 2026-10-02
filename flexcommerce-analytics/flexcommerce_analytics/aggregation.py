"""Daily revenue aggregation (``aggregate_analytics`` command and the nightly job)."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from flexcommerce_core.utils.products import is_installed

from .models import DailyRevenueSummary
from .reports import date_range, first_order_dates, sales_queryset

ZERO = Decimal("0.00")


def build_day(day):
    """Return ``{currency: fields}`` for one day."""
    result = {}
    if not is_installed("flexcommerce_orders"):
        return result
    from flexcommerce_orders.models import Order, OrderItem, Refund

    start, end = date_range(day, day)
    for row in (
        sales_queryset(day, day)
        .values("currency")
        .annotate(
            orders=Count("id"),
            gross=Sum("grand_total"),
            tax=Sum("tax_total"),
            shipping=Sum("shipping_cost"),
            disc=Sum("discount_amount"),
        )
    ):
        gross, tax = row["gross"] or ZERO, row["tax"] or ZERO
        result[row["currency"]] = {
            "order_count": row["orders"],
            "gross_revenue": gross,
            "net_revenue": gross - tax,
            "tax_collected": tax,
            "shipping_collected": row["shipping"] or ZERO,
            "discount_given": row["disc"] or ZERO,
            "average_order_value": (gross / row["orders"]).quantize(Decimal("0.01")) if row["orders"] else ZERO,
        }
    for row in (
        OrderItem.objects.filter(order__in=sales_queryset(day, day))
        .values("order__currency")
        .annotate(units=Sum("quantity"))
    ):
        result.setdefault(row["order__currency"], {})["units_sold"] = row["units"] or 0
    for row in (
        Refund.objects.filter(status=Refund.STATUS_PROCESSED, processed_at__gte=start, processed_at__lt=end)
        .values("order__currency")
        .annotate(total=Sum("amount"))
    ):
        result.setdefault(row["order__currency"], {})["refund_total"] = row["total"] or ZERO
    for row in (
        Order.objects.filter(status=Order.STATUS_CANCELLED, cancelled_at__gte=start, cancelled_at__lt=end)
        .values("currency")
        .annotate(n=Count("id"))
    ):
        result.setdefault(row["currency"], {})["cancelled_order_count"] = row["n"]
    firsts = first_order_dates()
    for order in sales_queryset(day, day).exclude(email="").only("email", "currency", "confirmed_at"):
        first = firsts.get(order.email)
        if first is not None and start <= first < end:
            entry = result.setdefault(order.currency, {})
            entry["new_customers"] = entry.get("new_customers", 0) + 1
    if is_installed("flexcommerce_cart"):
        from flexcommerce_cart.models import Cart

        abandoned = Cart.objects.filter(abandoned_notified_at__gte=start, abandoned_notified_at__lt=end)
        for row in abandoned.values("currency").annotate(n=Count("id")):
            result.setdefault(row["currency"], {})["abandoned_cart_count"] = row["n"]
    return result


def aggregate_range(start, end, rebuild=False):
    created = updated = 0
    day = start
    while day <= end:
        for currency, fields in build_day(day).items():
            existing = DailyRevenueSummary.objects.filter(date=day, currency=currency).first()
            if existing is None:
                DailyRevenueSummary.objects.create(date=day, currency=currency, **fields)
                created += 1
            elif rebuild:
                for key, value in fields.items():
                    setattr(existing, key, value)
                existing.save()
                updated += 1
        day += timedelta(days=1)
    return {"created": created, "updated": updated}


def aggregate_yesterday():
    """Job: (re)build yesterday's and today's summaries."""
    today = timezone.localdate()
    return aggregate_range(today - timedelta(days=1), today, rebuild=True)
