"""
FlexCommerce analytics reports — pure query functions returning JSON-friendly data.

Definitions
-----------
* An order counts as a **sale** when it is confirmed (paid, pay-on-delivery
  accepted, or confirmed by staff) and not cancelled. Sales are dated by
  ``confirmed_at``.
* **gross** = amount charged (incl. VAT and shipping); **net** = gross − VAT;
  **collected** = money actually received.
* **Refunds** are dated by when they were processed.
* Every money figure is grouped by currency.

Date arguments are ``datetime.date`` objects; ranges are inclusive and use the
project's ``TIME_ZONE``.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from flexcommerce_core.utils.products import is_installed

ZERO = Decimal("0.00")
PERIODS = {"daily": TruncDate, "weekly": TruncWeek, "monthly": TruncMonth}
MONEY = DecimalField(max_digits=16, decimal_places=2)


def date_range(start_date, end_date):
    """Timezone-aware ``[start, end)`` datetimes covering the inclusive date range (index-friendly)."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
    return start, end


def _money(value):
    return str((value or ZERO).quantize(Decimal("0.01")))


def _period(value):
    if value is None:
        return None
    return value.date().isoformat() if hasattr(value, "date") and callable(value.date) else value.isoformat()


def _orders():
    from flexcommerce_orders.models import Order

    return Order


def sales_queryset(start_date, end_date):
    Order = _orders()
    start, end = date_range(start_date, end_date)
    return Order.objects.filter(confirmed_at__gte=start, confirmed_at__lt=end).exclude(status=Order.STATUS_CANCELLED)


# ── Revenue ──────────────────────────────────────────────────────────────────


def revenue_report(start_date, end_date, period="daily") -> list:
    if not is_installed("flexcommerce_orders"):
        return []
    trunc = PERIODS.get(period, TruncDate)
    rows = (
        sales_queryset(start_date, end_date)
        .annotate(period=trunc("confirmed_at"))
        .values("period", "currency")
        .annotate(
            order_count=Count("id"),
            gross=Sum("grand_total"),
            tax=Sum("tax_total"),
            shipping=Sum("shipping_cost"),
            discounts=Sum("discount_amount"),
            collected=Sum("amount_paid"),
        )
        .order_by("period", "currency")
    )
    refunds = {}
    from flexcommerce_orders.models import Refund

    start, end = date_range(start_date, end_date)
    refund_rows = (
        Refund.objects.filter(status=Refund.STATUS_PROCESSED, processed_at__gte=start, processed_at__lt=end)
        .annotate(period=trunc("processed_at"))
        .values("period", "order__currency")
        .annotate(total=Sum("amount"))
    )
    for row in refund_rows:
        refunds[(_period(row["period"]), row["order__currency"])] = row["total"]
    result = []
    for row in rows:
        key = (_period(row["period"]), row["currency"])
        gross, tax = row["gross"] or ZERO, row["tax"] or ZERO
        result.append(
            {
                "period": key[0],
                "currency": row["currency"],
                "order_count": row["order_count"],
                "gross": _money(gross),
                "net": _money(gross - tax),
                "tax": _money(tax),
                "shipping": _money(row["shipping"]),
                "discounts": _money(row["discounts"]),
                "collected": _money(row["collected"]),
                "refunds": _money(refunds.pop(key, ZERO)),
                "average_order_value": _money(gross / row["order_count"]) if row["order_count"] else "0.00",
            }
        )
    for (period_value, currency), total in refunds.items():  # refund-only periods
        result.append(
            {
                "period": period_value,
                "currency": currency,
                "order_count": 0,
                "gross": "0.00",
                "net": "0.00",
                "tax": "0.00",
                "shipping": "0.00",
                "discounts": "0.00",
                "collected": "0.00",
                "refunds": _money(total),
                "average_order_value": "0.00",
            }
        )
    return sorted(result, key=lambda r: (r["period"] or "", r["currency"]))


# ── Products ─────────────────────────────────────────────────────────────────


def top_products_report(start_date, end_date, limit=10, by="revenue") -> list:
    if not is_installed("flexcommerce_orders"):
        return []
    from flexcommerce_orders.models import OrderItem

    Order = _orders()
    start, end = date_range(start_date, end_date)
    paid_line = ExpressionWrapper(F("line_total") - F("discount_amount"), output_field=MONEY)
    qs = (
        OrderItem.objects.filter(order__confirmed_at__gte=start, order__confirmed_at__lt=end)
        .exclude(order__status=Order.STATUS_CANCELLED)
        .values("object_id", "product_name", "product_sku", "order__currency")
        .annotate(
            quantity_sold=Sum("quantity"),
            revenue=Sum(paid_line),
            order_count=Count("order_id", distinct=True),
        )
        .order_by("-revenue" if by == "revenue" else "-quantity_sold", "product_name")[: max(1, min(int(limit), 500))]
    )
    return [
        {
            "product_id": str(r["object_id"]) if r["object_id"] else None,
            "product_name": r["product_name"],
            "sku": r["product_sku"],
            "currency": r["order__currency"],
            "quantity_sold": r["quantity_sold"],
            "revenue": _money(r["revenue"]),
            "order_count": r["order_count"],
        }
        for r in qs
    ]


# ── Carts ────────────────────────────────────────────────────────────────────


def abandoned_cart_report(start_date, end_date) -> dict:
    if not is_installed("flexcommerce_cart"):
        return {}
    from flexcommerce_cart.models import Cart

    start, end = date_range(start_date, end_date)
    abandoned = Cart.objects.filter(abandoned_notified_at__gte=start, abandoned_notified_at__lt=end)
    totals = abandoned.aggregate(count=Count("id"), value=Sum("total_amount"))
    recovered = abandoned.filter(status=Cart.STATUS_ORDERED).aggregate(count=Count("id"), value=Sum("total_amount"))
    count = totals["count"] or 0
    by_day = (
        abandoned.annotate(day=TruncDate("abandoned_notified_at"))
        .values("day")
        .annotate(count=Count("id"), value=Sum("total_amount"))
        .order_by("day")
    )
    return {
        "total_abandoned": count,
        "total_value": _money(totals["value"]),
        "average_value": _money((totals["value"] or ZERO) / count) if count else "0.00",
        "recovered": recovered["count"] or 0,
        "recovered_value": _money(recovered["value"]),
        "recovery_rate": round((recovered["count"] or 0) / count * 100, 2) if count else 0.0,
        "by_day": [{"day": _period(r["day"]), "count": r["count"], "value": _money(r["value"])} for r in by_day],
    }


def conversion_report(start_date, end_date) -> dict:
    """Carts that received items vs carts that became orders."""
    if not is_installed("flexcommerce_cart"):
        return {}
    from flexcommerce_cart.models import Cart

    start, end = date_range(start_date, end_date)
    carts = Cart.objects.filter(created_at__gte=start, created_at__lt=end)
    with_items = carts.filter(Q(items_count__gt=0) | Q(status__in=[Cart.STATUS_ORDERED, Cart.STATUS_MERGED]))
    created = with_items.count()
    ordered = carts.filter(status=Cart.STATUS_ORDERED).count()
    return {
        "carts_with_items": created,
        "orders": ordered,
        "conversion_rate": round(ordered / created * 100, 2) if created else 0.0,
    }


# ── Coupons ──────────────────────────────────────────────────────────────────


def coupon_performance_report(start_date, end_date) -> list:
    if not is_installed("flexcommerce_discounts"):
        return []
    from flexcommerce_discounts.models import CouponUsage

    start, end = date_range(start_date, end_date)
    rows = (
        CouponUsage.objects.filter(created_at__gte=start, created_at__lt=end)
        .values(code=F("coupon__code"))
        .annotate(usage_count=Count("id"), total_discount=Sum("discount_applied"))
        .order_by("-usage_count", "code")
    )
    revenue = {}
    if is_installed("flexcommerce_orders"):
        codes = [r["code"] for r in rows]
        for row in (
            sales_queryset(start_date, end_date)
            .filter(coupon_code__in=codes)
            .values("coupon_code")
            .annotate(rev=Sum("grand_total"))
        ):
            revenue[row["coupon_code"]] = row["rev"]
    return [
        {
            "code": r["code"],
            "usage_count": r["usage_count"],
            "total_discount": _money(r["total_discount"]),
            "orders_revenue": _money(revenue.get(r["code"])),
        }
        for r in rows
    ]


# ── Marketplace ──────────────────────────────────────────────────────────────


def vendor_performance_report(start_date, end_date) -> list:
    if is_installed("flexcommerce_marketplace"):
        from flexcommerce_marketplace.models import VendorOrder

        start, end = date_range(start_date, end_date)
        rows = (
            VendorOrder.objects.filter(order__confirmed_at__gte=start, order__confirmed_at__lt=end)
            .exclude(status=VendorOrder.STATUS_CANCELLED)
            .values("vendor_id", "vendor__name")
            .annotate(
                order_count=Count("id"),
                gross=Sum("gross_amount"),
                commission=Sum("commission_amount"),
                refunded=Sum("refunded_amount"),
            )
            .order_by("-gross")
        )
        return [
            {
                "vendor_id": str(r["vendor_id"]),
                "vendor_name": r["vendor__name"],
                "order_count": r["order_count"],
                "gross_revenue": _money(r["gross"]),
                "commission": _money(r["commission"]),
                "refunded": _money(r["refunded"]),
            }
            for r in rows
        ]
    if not is_installed("flexcommerce_orders"):
        return []
    from flexcommerce_orders.models import OrderItem

    Order = _orders()
    start, end = date_range(start_date, end_date)
    commission = ExpressionWrapper((F("line_total") - F("discount_amount")) * F("commission_rate"), output_field=MONEY)
    rows = (
        OrderItem.objects.filter(order__confirmed_at__gte=start, order__confirmed_at__lt=end, vendor_id__isnull=False)
        .exclude(order__status=Order.STATUS_CANCELLED)
        .values("vendor_id")
        .annotate(
            items_sold=Sum("quantity"),
            gross=Sum(F("line_total") - F("discount_amount"), output_field=MONEY),
            commission=Sum(commission),
            order_count=Count("order_id", distinct=True),
        )
        .order_by("-gross")
    )
    return [
        {
            "vendor_id": str(r["vendor_id"]),
            "items_sold": r["items_sold"],
            "gross_revenue": _money(r["gross"]),
            "commission": _money(r["commission"]),
            "order_count": r["order_count"],
        }
        for r in rows
    ]


# ── Breakdowns & customers ───────────────────────────────────────────────────


def sales_by_state_report(start_date, end_date) -> list:
    if not is_installed("flexcommerce_orders"):
        return []
    totals = {}
    for order in sales_queryset(start_date, end_date).only("shipping_address", "grand_total", "currency").iterator():
        state = str((order.shipping_address or {}).get("state", "") or "Unknown").strip().title()
        entry = totals.setdefault((state, order.currency), {"orders": 0, "revenue": ZERO})
        entry["orders"] += 1
        entry["revenue"] += order.grand_total
    rows = [
        {"state": s, "currency": c, "order_count": v["orders"], "revenue": _money(v["revenue"])}
        for (s, c), v in totals.items()
    ]
    return sorted(rows, key=lambda r: Decimal(r["revenue"]), reverse=True)


def payment_methods_report(start_date, end_date) -> list:
    if not is_installed("flexcommerce_orders"):
        return []
    rows = (
        sales_queryset(start_date, end_date)
        .values("payment_method", "payment_provider", "currency")
        .annotate(order_count=Count("id"), revenue=Sum("grand_total"))
        .order_by("-revenue")
    )
    return [
        {
            "payment_method": r["payment_method"],
            "provider": r["payment_provider"],
            "currency": r["currency"],
            "order_count": r["order_count"],
            "revenue": _money(r["revenue"]),
        }
        for r in rows
    ]


def customer_report(start_date, end_date) -> dict:
    if not is_installed("flexcommerce_orders"):
        return {}
    Order = _orders()
    sales = sales_queryset(start_date, end_date).exclude(email="")
    first_orders = (
        Order.objects.exclude(status=Order.STATUS_CANCELLED)
        .exclude(confirmed_at__isnull=True)
        .filter(email=OuterRef("email"))
        .order_by("confirmed_at")
        .values("confirmed_at")[:1]
    )
    start, _ = date_range(start_date, end_date)
    customers = sales.values("email").annotate(first=Subquery(first_orders), orders=Count("id"))
    new = sum(1 for c in customers if c["first"] and c["first"] >= start)
    total = len(customers)
    return {
        "customers": total,
        "new_customers": new,
        "returning_customers": total - new,
        "repeat_purchase_rate": round((total - new) / total * 100, 2) if total else 0.0,
    }


def dashboard_summary(days=30) -> dict:
    """KPIs for today, the last 7 days and the last ``days`` days."""
    today = timezone.localdate()
    out = {}
    for label, start in (
        ("today", today),
        ("last_7_days", today - timedelta(days=6)),
        (f"last_{days}_days", today - timedelta(days=days - 1)),
    ):
        rows = revenue_report(start, today, period="monthly") if is_installed("flexcommerce_orders") else []
        by_currency = {}
        for row in rows:
            entry = by_currency.setdefault(row["currency"], {"orders": 0, "gross": ZERO, "refunds": ZERO})
            entry["orders"] += row["order_count"]
            entry["gross"] += Decimal(row["gross"])
            entry["refunds"] += Decimal(row["refunds"])
        out[label] = {
            c: {
                "orders": v["orders"],
                "gross": _money(v["gross"]),
                "refunds": _money(v["refunds"]),
                "average_order_value": _money(v["gross"] / v["orders"]) if v["orders"] else "0.00",
            }
            for c, v in by_currency.items()
        }
    if is_installed("flexcommerce_orders"):
        Order = _orders()
        out["needs_attention"] = {
            "to_ship": Order.objects.filter(status__in=[Order.STATUS_CONFIRMED, Order.STATUS_PROCESSING]).count(),
            "awaiting_payment": Order.objects.filter(status=Order.STATUS_PENDING).count(),
        }
        from flexcommerce_orders.models import Refund, ReturnRequest

        out["needs_attention"]["refunds_to_process"] = Refund.objects.filter(
            status__in=[Refund.STATUS_REQUESTED, Refund.STATUS_APPROVED]
        ).count()
        out["needs_attention"]["returns_to_review"] = ReturnRequest.objects.filter(
            status=ReturnRequest.STATUS_PENDING
        ).count()
    return out


def first_order_dates():
    """``{email: first confirmed order datetime}`` (used by the daily aggregation)."""
    Order = _orders()
    return dict(
        Order.objects.exclude(status=Order.STATUS_CANCELLED)
        .exclude(email="")
        .exclude(confirmed_at__isnull=True)
        .values("email")
        .annotate(first=Min("confirmed_at"))
        .values_list("email", "first")
    )


REPORTS = {
    "revenue": revenue_report,
    "top_products": top_products_report,
    "abandoned_carts": lambda s, e: abandoned_cart_report(s, e).get("by_day", []),
    "coupon_performance": coupon_performance_report,
    "vendor_performance": vendor_performance_report,
    "sales_by_state": sales_by_state_report,
    "payment_methods": payment_methods_report,
}
