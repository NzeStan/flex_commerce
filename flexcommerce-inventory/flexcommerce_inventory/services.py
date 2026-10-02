"""
Inventory service API used by cart and checkout.

All functions accept product instances of any model listed in PRODUCT_MODELS.
"""

from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from flexcommerce_core.exceptions import InsufficientStockError

from .conf import inventory_setting
from .models import InventoryItem, StockAlert, StockReservation


def _key(product):
    return ContentType.objects.get_for_model(product).pk, product.pk


def get_item(product):
    """Tracked ``InventoryItem`` for ``product`` or ``None`` if untracked."""
    if inventory_setting("INVENTORY_TRACK_BY_DEFAULT"):
        return InventoryItem.get_for_product(product, create=True)
    return InventoryItem.get_for_product(product, create=False)


def availability_map(products):
    """``{product.pk: available units or None (untracked)}`` in one query per type."""
    by_ct = {}
    for p in products:
        by_ct.setdefault(ContentType.objects.get_for_model(p).pk, []).append(p.pk)
    result = {p.pk: (0 if inventory_setting("INVENTORY_TRACK_BY_DEFAULT") else None) for p in products}
    for ct_id, ids in by_ct.items():
        for item in InventoryItem.objects.filter(content_type_id=ct_id, object_id__in=ids):
            result[item.object_id] = None if item.oversell_allowed() else item.available
    return result


def check_available(product, quantity):
    """Raise ``InsufficientStockError`` if a tracked product lacks stock."""
    item = get_item(product)
    if item is not None and not item.is_available(quantity):
        raise InsufficientStockError(available=item.available, requested=quantity, extra={"sku": item.sku})


def reserve_lines(lines, order_ref, ttl_minutes=None):
    """
    Reserve stock for ``[(product, quantity), ...]`` all-or-nothing (if any line
    fails, nothing stays reserved). Items are processed in a stable order so
    concurrent checkouts can never deadlock each other.
    """
    reservations = []
    with transaction.atomic():
        for product, quantity in sorted(lines, key=lambda line: (_key(line[0])[0], str(line[0].pk))):
            item = get_item(product)
            if item is None:
                continue
            reservations.append(item.reserve(quantity, order_ref=order_ref, ttl_minutes=ttl_minutes))
    return reservations


def _for_order(order_ref, statuses):
    return StockReservation.objects.filter(order_ref=order_ref, status__in=statuses).select_related("inventory_item")


def confirm_order(order_ref):
    """Convert all reservations of an order into sales. Idempotent."""
    count = 0
    with transaction.atomic():
        for res in _for_order(order_ref, [StockReservation.STATUS_PENDING, StockReservation.STATUS_RELEASED]):
            count += res.inventory_item.confirm_sale(reservation_id=res.pk)
    return count


def release_order(order_ref):
    """Release all pending reservations of an order (cancel / payment failure). Idempotent."""
    count = 0
    with transaction.atomic():
        for res in _for_order(order_ref, [StockReservation.STATUS_PENDING]):
            count += res.inventory_item.release(reservation_id=res.pk)
    return count


def return_to_stock(product, quantity, reference="", note=""):
    item = get_item(product)
    if item is not None:
        item.return_stock(quantity, note=note, reference=reference)
    return item


def extend_order_reservations(order_ref, minutes):
    """Push the expiry of an order's pending reservations (e.g. bank transfer)."""
    return _for_order(order_ref, [StockReservation.STATUS_PENDING]).update(
        expires_at=timezone.now() + timedelta(minutes=minutes) if minutes else None
    )


def release_expired(limit=1000):
    """Job: release reservations whose hold has expired."""
    expired = list(
        StockReservation.objects.filter(status=StockReservation.STATUS_PENDING, expires_at__lt=timezone.now())
        .select_related("inventory_item")
        .order_by("expires_at")[:limit]
    )
    released = sum(1 for res in expired if res.inventory_item.release(reservation_id=res.pk))
    return {"released": released}


def subscribe_alert(product, user=None, email="", phone=""):
    ct = ContentType.objects.get_for_model(product)
    lookup = {"content_type": ct, "object_id": product.pk, "notified_at__isnull": True}
    if user is not None and user.is_authenticated:
        alert, _ = StockAlert.objects.get_or_create(**lookup, user=user, defaults={"email": email, "phone": phone})
    else:
        alert, _ = StockAlert.objects.get_or_create(**lookup, user=None, email=email.lower(), defaults={"phone": phone})
    return alert


def mark_alerts_notified(alerts):
    StockAlert.objects.filter(pk__in=[a.pk for a in alerts]).update(notified_at=timezone.now())
