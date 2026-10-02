"""
Marketplace services.

* ``split_order``   – ``order.created`` hook (inside the checkout transaction):
                      one ``VendorOrder`` per vendor, commission snapshotted.
* order signal receivers keep vendor orders in sync (confirm / cancel / deliver).
* ``allocate_refund`` – refunds reduce the right vendor's earnings.
* ``generate_payouts`` – job: settle earnings whose hold period has passed.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from flexcommerce_core.events import emit
from flexcommerce_core.exceptions import (
    FlexCommerceError,
    NotFoundError,
    PermissionDeniedError,
    VendorError,
    VendorNotApprovedError,
)
from flexcommerce_core.utils.vat import ZERO, round_price

from . import signals
from .conf import marketplace_setting
from .models import Payout, Vendor, VendorMember, VendorOrder

# ── Vendors ──────────────────────────────────────────────────────────────────


def vendor_for_user(user, approved_only=True):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    membership = VendorMember.objects.select_related("vendor").filter(user=user).first()
    vendor = membership.vendor if membership else None
    if vendor is None or (approved_only and not vendor.is_approved):
        return None
    return vendor


def vendor_id_for_user(user):
    """Hook ``catalog.vendor_id_for_user``: lets approved vendors manage their own products."""
    vendor = vendor_for_user(user)
    return vendor.pk if vendor else None


def require_vendor(user, approved=True):
    vendor = vendor_for_user(user, approved_only=False)
    if vendor is None:
        raise PermissionDeniedError("You do not have a vendor account.", code="not_a_vendor")
    if approved and not vendor.is_approved:
        raise VendorNotApprovedError(extra={"status": vendor.status})
    return vendor


@transaction.atomic
def apply(user, **data) -> Vendor:
    if VendorMember.objects.filter(user=user).exists():
        raise VendorError("You already belong to a vendor account.", code="already_vendor")
    vendor = Vendor.objects.create(owner=user, **data)
    VendorMember.objects.create(vendor=vendor, user=user, role=VendorMember.ROLE_OWNER)
    emit("vendor.applied", signal=signals.vendor_applied, sender=Vendor, vendor=vendor)
    if marketplace_setting("VENDOR_AUTO_APPROVE"):
        set_status(vendor, Vendor.STATUS_APPROVED)
    return vendor


def set_status(vendor, status, reason=""):
    vendor.status = status
    vendor.status_reason = reason[:255]
    fields = ["status", "status_reason", "updated_at"]
    if status == Vendor.STATUS_APPROVED and vendor.approved_at is None:
        vendor.approved_at = timezone.now()
        fields.append("approved_at")
    vendor.save(update_fields=fields)
    if status == Vendor.STATUS_APPROVED:
        emit(
            "vendor.approved",
            signal=signals.vendor_approved,
            sender=Vendor,
            payload={"vendor_id": str(vendor.pk), "name": vendor.name},
            vendor=vendor,
        )
    else:
        emit("vendor.status_changed", signal=signals.vendor_status_changed, sender=Vendor, vendor=vendor, status=status)
    return vendor


# ── Order splitting & lifecycle ──────────────────────────────────────────────


def split_order(order, **kwargs):
    """``order.created`` hook."""
    items_by_vendor = defaultdict(list)
    for item in order.items.all():
        if item.vendor_id:
            items_by_vendor[item.vendor_id].append(item)
    if not items_by_vendor:
        return []
    vendors = Vendor.objects.in_bulk(list(items_by_vendor))
    created = []
    for vendor_id, items in items_by_vendor.items():
        vendor = vendors.get(vendor_id)
        if vendor is None:
            continue
        rate = vendor.effective_commission_rate
        gross = round_price(sum((i.paid_line_total for i in items), ZERO))
        for item in items:
            type(item).objects.filter(pk=item.pk).update(commission_rate=rate)
        created.append(
            VendorOrder.objects.create(
                order=order,
                vendor=vendor,
                gross_amount=gross,
                commission_rate=rate,
                commission_amount=round_price(gross * rate),
            )
        )
    for vendor_order in created:
        emit("vendor_order.created", signal=signals.vendor_order_created, sender=VendorOrder, vendor_order=vendor_order)
    return created


def hold_days():
    days = marketplace_setting("VENDOR_PAYOUT_HOLD_DAYS")
    if days is None:
        from flexcommerce_core.conf import fc_setting

        days = fc_setting("RETURN_WINDOW_DAYS", 7) or 0
    return int(days)


def on_order_confirmed(sender, order, **kwargs):
    VendorOrder.objects.filter(order=order, status=VendorOrder.STATUS_PENDING).update(
        status=VendorOrder.STATUS_CONFIRMED
    )


def on_order_cancelled(sender, order, **kwargs):
    VendorOrder.objects.filter(order=order, payout__isnull=True).exclude(status=VendorOrder.STATUS_DELIVERED).update(
        status=VendorOrder.STATUS_CANCELLED
    )


def on_shipment_created(sender, shipment, order, **kwargs):
    vendor_ids = {i.vendor_id for i in order.items.all() if i.vendor_id and i.quantity_shipped >= i.quantity}
    for vendor_order in VendorOrder.objects.filter(
        order=order, vendor_id__in=vendor_ids, status=VendorOrder.STATUS_CONFIRMED
    ):
        items = [i for i in order.items.all() if i.vendor_id == vendor_order.vendor_id]
        if all(i.quantity_shipped >= i.quantity for i in items):
            VendorOrder.objects.filter(pk=vendor_order.pk).update(status=VendorOrder.STATUS_SHIPPED)


def on_order_delivered(sender, order, **kwargs):
    now = timezone.now()
    VendorOrder.objects.filter(order=order).exclude(status=VendorOrder.STATUS_CANCELLED).update(
        status=VendorOrder.STATUS_DELIVERED, delivered_at=now, available_at=now + timedelta(days=hold_days())
    )


def allocate_refund(sender, refund, order, **kwargs):
    """``refund_processed`` receiver: charge the refund to the vendors whose items were refunded."""
    vendor_orders = {vo.vendor_id: vo for vo in VendorOrder.objects.filter(order=order)}
    if not vendor_orders:
        return
    items = {str(i.pk): i for i in order.items.all()}
    shares = defaultdict(lambda: ZERO)
    if refund.items:
        for entry in refund.items:
            item = items.get(str(entry.get("order_item_id")))
            if item is not None and item.vendor_id in vendor_orders:
                shares[item.vendor_id] += round_price(item.unit_paid * int(entry.get("quantity", 0)))
    else:
        total = sum((i.paid_line_total for i in items.values()), ZERO)
        for item in items.values():
            if item.vendor_id in vendor_orders and total:
                shares[item.vendor_id] += round_price(refund.amount * item.paid_line_total / total)
    for vendor_id, amount in shares.items():
        VendorOrder.objects.filter(pk=vendor_orders[vendor_id].pk).update(refunded_amount=F("refunded_amount") + amount)


def on_rating_changed(sender, content_type, object_id, **kwargs):
    """Vendor rating = review-weighted average over the vendor's catalog products."""
    from flexcommerce_core.utils.products import is_installed

    if not is_installed("flexcommerce_catalog"):
        return
    from flexcommerce_catalog.models import Product

    if content_type.model_class() is not Product:
        return
    vendor_id = Product.objects.filter(pk=object_id).values_list("vendor_id", flat=True).first()
    if not vendor_id:
        return
    rows = Product.objects.filter(vendor_id=vendor_id, rating_count__gt=0).values_list("rating_avg", "rating_count")
    count = sum(c for _, c in rows)
    average = round_price(sum((a * c for a, c in rows), ZERO) / count) if count else ZERO
    Vendor.objects.filter(pk=vendor_id).update(rating_avg=average, rating_count=count)


# ── Vendor fulfilment ────────────────────────────────────────────────────────


def ship_vendor_order(vendor_order, actor="vendor", **shipment):
    from flexcommerce_orders.services import OrderService

    if vendor_order.status != VendorOrder.STATUS_CONFIRMED:
        raise VendorError(
            "This order is not ready to ship.", code="not_shippable", extra={"status": vendor_order.status}
        )
    result = OrderService(vendor_order.order).create_shipment(vendor_id=vendor_order.vendor_id, actor=actor, **shipment)
    vendor_order.refresh_from_db()
    return result


def vendor_summary(vendor):
    orders = VendorOrder.objects.filter(vendor=vendor).exclude(status=VendorOrder.STATUS_CANCELLED)
    agg = orders.aggregate(
        gross=Sum("gross_amount"), commission=Sum("commission_amount"), refunded=Sum("refunded_amount")
    )
    counts = {status: 0 for status, _ in VendorOrder.STATUS_CHOICES}
    for row in VendorOrder.objects.filter(vendor=vendor).values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    available = sum((vo.net_amount for vo in payable_orders(vendor)), ZERO)
    paid = Payout.objects.filter(vendor=vendor, status=Payout.STATUS_PAID).aggregate(t=Sum("amount"))["t"] or ZERO
    return {
        "gross_sales": round_price(agg["gross"] or ZERO),
        "commission": round_price(agg["commission"] or ZERO),
        "refunded": round_price(agg["refunded"] or ZERO),
        "available_for_payout": round_price(available),
        "paid_out": round_price(paid),
        "orders_by_status": counts,
    }


# ── Payouts ──────────────────────────────────────────────────────────────────


def payable_orders(vendor, now=None):
    return VendorOrder.objects.filter(
        vendor=vendor, status=VendorOrder.STATUS_DELIVERED, payout__isnull=True, available_at__lte=now or timezone.now()
    )


@transaction.atomic
def create_payout(vendor):
    orders = list(payable_orders(vendor).select_for_update())
    amount = round_price(sum((vo.net_amount for vo in orders), ZERO))
    if not orders or amount < Decimal(str(marketplace_setting("VENDOR_MINIMUM_PAYOUT"))):
        return None
    payout = Payout.objects.create(
        vendor=vendor,
        amount=amount,
        currency=orders[0].order.currency,
        bank_details={
            "bank_name": vendor.bank_name,
            "bank_code": vendor.bank_code,
            "account_number": vendor.account_number,
            "account_name": vendor.account_name,
        },
    )
    VendorOrder.objects.filter(pk__in=[vo.pk for vo in orders]).update(payout=payout)
    emit(
        "payout.created",
        signal=signals.payout_created,
        sender=Payout,
        payload={"payout_id": str(payout.pk), "vendor_id": str(vendor.pk), "amount": str(amount)},
        payout=payout,
    )
    return payout


def generate_payouts():
    """Job: create payouts for every approved vendor with settled earnings."""
    created = [create_payout(v) for v in Vendor.objects.filter(status=Vendor.STATUS_APPROVED)]
    return {"payouts": len([p for p in created if p])}


@transaction.atomic
def mark_payout(payout, paid: bool, reference="", reason=""):
    payout = Payout.objects.select_for_update().get(pk=payout.pk)
    if payout.status in (Payout.STATUS_PAID,):
        raise FlexCommerceError("This payout has already been paid.", code="payout_already_paid")
    payout.status = Payout.STATUS_PAID if paid else Payout.STATUS_FAILED
    payout.reference = reference or payout.reference
    payout.failure_reason = "" if paid else reason[:500]
    payout.processed_at = timezone.now()
    payout.save(update_fields=["status", "reference", "failure_reason", "processed_at", "updated_at"])
    if not paid:
        VendorOrder.objects.filter(payout=payout).update(payout=None)  # earnings become payable again
    emit("payout.updated", signal=signals.payout_updated, sender=Payout, payout=payout)
    return payout


def get_vendor_order(vendor, pk):
    vendor_order = VendorOrder.objects.select_related("order").filter(vendor=vendor, pk=pk).first()
    if vendor_order is None:
        raise NotFoundError()
    return vendor_order
