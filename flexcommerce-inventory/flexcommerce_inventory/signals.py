"""
Inventory signals (sent after commit via ``flexcommerce_core.events.emit``).

low_stock(item)              available crossed down to the low-stock threshold
out_of_stock(item)           available reached 0
restocked(item, quantity)    available went from 0 to > 0
back_in_stock(item, alerts)  same moment, with the pending StockAlert rows
stock_changed(item)          any change in on_hand / reserved
"""

from django.dispatch import Signal

from flexcommerce_core.events import emit

low_stock = Signal()
out_of_stock = Signal()
restocked = Signal()
back_in_stock = Signal()
stock_changed = Signal()


def _payload(item):
    return {
        "inventory_item_id": str(item.pk),
        "sku": item.sku,
        "product_type": item.content_type.model_class()._meta.label_lower if item.content_type_id else None,
        "product_id": str(item.object_id),
        "on_hand": item.on_hand,
        "reserved": item.reserved,
        "available": item.available,
    }


def emit_stock_events(item, before_available):
    from .models import InventoryItem, StockAlert

    after = item.available
    sender = InventoryItem
    emit("inventory.stock_changed", signal=stock_changed, sender=sender, item=item)
    threshold = item.low_stock_threshold
    if after == 0 and before_available > 0:
        emit(
            "inventory.out_of_stock",
            signal=out_of_stock,
            sender=sender,
            payload=_payload(item),
            item=item,
        )
    elif after <= threshold < before_available:
        emit(
            "inventory.low_stock",
            signal=low_stock,
            sender=sender,
            payload=_payload(item),
            item=item,
        )
    if before_available == 0 and after > 0:
        emit(
            "inventory.restocked",
            signal=restocked,
            sender=sender,
            payload=_payload(item),
            item=item,
            quantity=after,
        )
        alerts = list(
            StockAlert.objects.filter(
                content_type_id=item.content_type_id,
                object_id=item.object_id,
                notified_at__isnull=True,
            ).select_related("user")
        )
        if alerts:
            emit(
                "inventory.back_in_stock",
                signal=back_in_stock,
                sender=sender,
                item=item,
                alerts=alerts,
            )
