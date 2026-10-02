"""
Order service: the only place order state changes.

Every method locks the order row, applies the change and its side effects
(stock, coupons, refunds) atomically, records the audit log and the customer
timeline, and emits the matching event after commit.
"""

import logging
import secrets
from collections import defaultdict
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.module_loading import import_string

from flexcommerce_core import hooks
from flexcommerce_core.events import emit
from flexcommerce_core.exceptions import (
    InvalidOrderTransitionError,
    OrderCancellationError,
    OrderError,
    RefundError,
    ReturnError,
)
from flexcommerce_core.models import AuditLog
from flexcommerce_core.utils.products import (
    is_installed,
    prefetch_products,
    product_name,
    product_sku,
)
from flexcommerce_core.utils.vat import ZERO, round_price, to_decimal

from . import signals
from .conf import orders_setting
from .models import (
    Order,
    OrderEvent,
    OrderItem,
    OrderStateMachine,
    Refund,
    ReturnRequest,
    Shipment,
    ShipmentEvent,
)

logger = logging.getLogger("flexcommerce.orders")

TRANSITION_SIGNALS = {
    Order.STATUS_CONFIRMED: ("order.confirmed", signals.order_confirmed),
    Order.STATUS_PROCESSING: ("order.processing", signals.order_processing),
    Order.STATUS_PARTIALLY_SHIPPED: ("order.partially_shipped", signals.order_partially_shipped),
    Order.STATUS_SHIPPED: ("order.shipped", signals.order_shipped),
    Order.STATUS_DELIVERED: ("order.delivered", signals.order_delivered),
    Order.STATUS_CANCELLED: ("order.cancelled", signals.order_cancelled),
    Order.STATUS_REFUNDED: ("order.refunded", signals.order_refunded),
    Order.STATUS_PARTIALLY_REFUNDED: ("order.partially_refunded", signals.order_partially_refunded),
}

TIMELINE = {
    Order.STATUS_CONFIRMED: "Your order has been confirmed.",
    Order.STATUS_PROCESSING: "Your order is being prepared.",
    Order.STATUS_PARTIALLY_SHIPPED: "Part of your order has been shipped.",
    Order.STATUS_SHIPPED: "Your order has been shipped.",
    Order.STATUS_DELIVERED: "Your order has been delivered.",
    Order.STATUS_CANCELLED: "Your order was cancelled.",
    Order.STATUS_REFUNDED: "Your order has been refunded.",
    Order.STATUS_PARTIALLY_REFUNDED: "Part of your order has been refunded.",
}

TIMESTAMPS = {
    Order.STATUS_CONFIRMED: "confirmed_at",
    Order.STATUS_SHIPPED: "shipped_at",
    Order.STATUS_PARTIALLY_SHIPPED: "shipped_at",
    Order.STATUS_DELIVERED: "delivered_at",
    Order.STATUS_CANCELLED: "cancelled_at",
}


def generate_order_number() -> str:
    path = orders_setting("ORDER_NUMBER_GENERATOR")
    if path:
        return str(import_string(path)())
    return f"{orders_setting('ORDER_NUMBER_PREFIX')}{timezone.now():%y%m%d}{secrets.randbelow(10**8):08d}"


def order_payload(order) -> dict:
    return {
        "id": str(order.pk),
        "order_number": order.order_number,
        "status": order.status,
        "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "grand_total": str(order.grand_total),
        "amount_paid": str(order.amount_paid),
        "currency": order.currency,
        "email": order.customer_email,
        "user_id": str(order.user_id) if order.user_id else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "items": [
            {
                "product_id": str(i.object_id) if i.object_id else None,
                "name": i.product_name,
                "sku": i.product_sku,
                "quantity": i.quantity,
                "unit_price": str(i.unit_price_gross),
                "vendor_id": str(i.vendor_id) if i.vendor_id else None,
            }
            for i in order.items.all()
        ],
    }


def _snapshot(product):
    snap = getattr(product, "order_snapshot", None)
    if callable(snap):
        try:
            return snap()
        except Exception:  # never fail an order because of display data
            logger.exception("order_snapshot() failed for %r", product)
    return {}


# ── Placement ────────────────────────────────────────────────────────────────


@transaction.atomic
def place_order(
    *,
    lines,
    subtotal,
    discount,
    tax_total,
    shipping_cost,
    shipping_vat,
    grand_total,
    currency,
    shipping_address,
    billing_address=None,
    payment_method="",
    user=None,
    email="",
    phone="",
    session_key="",
    cart_id=None,
    idempotency_key="",
    coupon_code="",
    customer_note="",
    shipping_method=None,
    pickup_station=None,
    estimated_delivery=None,
    payment_due_at=None,
    extra=None,
) -> Order:
    """
    Create an order from priced lines (``PricedLine`` with ``discount`` allocated).
    Runs ``order.created`` hooks inside the transaction and emits ``order_created``
    after commit. Raises ``IntegrityError`` on a duplicate ``idempotency_key``.
    """
    fields = dict(
        user=user if getattr(user, "is_authenticated", False) else None,
        email=(email or getattr(user, "email", "") or "").lower(),
        phone=phone or "",
        session_key=session_key or "",
        cart_id=cart_id,
        idempotency_key=idempotency_key or "",
        shipping_address=shipping_address or {},
        billing_address=billing_address or shipping_address or {},
        subtotal=round_price(subtotal),
        discount_amount=round_price(discount),
        shipping_cost=round_price(shipping_cost),
        shipping_vat=round_price(shipping_vat),
        tax_total=round_price(tax_total),
        grand_total=round_price(grand_total),
        currency=currency,
        coupon_code=coupon_code or "",
        payment_method=payment_method,
        customer_note=customer_note or "",
        shipping_method_id=getattr(shipping_method, "pk", None),
        shipping_method_name=getattr(shipping_method, "name", "") or "",
        pickup_station=pickup_station or {},
        estimated_delivery_from=estimated_delivery[0] if estimated_delivery else None,
        estimated_delivery_to=estimated_delivery[1] if estimated_delivery else None,
        payment_due_at=payment_due_at,
        extra_data=extra or {},
    )
    order = None
    for attempt in range(6):
        try:
            with transaction.atomic():
                order = Order.objects.create(order_number=generate_order_number(), **fields)
            break
        except IntegrityError:
            if idempotency_key and Order.objects.filter(idempotency_key=idempotency_key).exists():
                raise
            if attempt == 5:
                raise
    OrderItem.objects.bulk_create(
        [
            OrderItem(
                order=order,
                content_type_id=line.content_type_id,
                object_id=line.product.pk,
                parent_product_id=line.parent_id or None,
                product_name=product_name(line.product),
                product_sku=product_sku(line.product),
                product_data=_snapshot(line.product),
                quantity=line.quantity,
                unit_price_net=line.unit_net,
                unit_vat=line.unit_vat,
                unit_price_gross=line.unit_gross,
                vat_rate=line.rate,
                line_total=line.line_gross,
                line_vat=line.line_vat,
                discount_amount=line.discount,
                vendor_id=line.vendor_id or None,
                commission_rate=getattr(line, "commission_rate", ZERO) or ZERO,
            )
            for line in lines
        ]
    )
    OrderEvent.objects.create(order=order, event="placed", message="Your order has been placed.", actor="checkout")
    AuditLog.log(order, AuditLog.ACTION_CREATE, actor=str(user) if order.user_id else "guest")
    hooks.run("order.created", order=order, lines=lines)
    emit(
        "order.created",
        signal=signals.order_created,
        sender=Order,
        payload=order_payload(order),
        order=order,
    )
    return order


# ── Lifecycle ────────────────────────────────────────────────────────────────


class OrderService:
    def __init__(self, order: Order):
        self.order = order

    # helpers
    def _lock(self) -> Order:
        locked = Order.objects.select_for_update().get(pk=self.order.pk)
        self.order.__dict__.update({f.attname: getattr(locked, f.attname) for f in Order._meta.concrete_fields})
        return self.order

    def _save(self, *fields):
        self.order.save(update_fields=[*fields, "updated_at"])

    def event(self, event, message, actor="system", visible=True, **data):
        return OrderEvent.objects.create(
            order=self.order,
            event=event,
            message=message,
            actor=str(actor)[:255],
            is_customer_visible=visible,
            data=data,
        )

    def _emit_status(self, from_state, actor):
        name, signal = TRANSITION_SIGNALS[self.order.status]
        emit(
            name,
            signal=signal,
            sender=Order,
            payload=order_payload(self.order),
            order=self.order,
            from_state=from_state,
            actor=str(actor),
        )

    def _set_status(self, to_state, actor="system", note=""):
        order = self.order
        from_state = order.status
        if from_state == to_state:
            return False
        if not OrderStateMachine(order).can_transition(to_state):
            raise InvalidOrderTransitionError(
                f"Cannot transition from '{from_state}' to '{to_state}'.",
                extra={
                    "from": from_state,
                    "to": to_state,
                    "allowed": OrderStateMachine(order).allowed_transitions(),
                },
            )
        order.status = to_state
        fields = ["status"]
        stamp = TIMESTAMPS.get(to_state)
        if stamp and getattr(order, stamp) is None:
            setattr(order, stamp, timezone.now())
            fields.append(stamp)
        self._save(*fields)
        AuditLog.log(
            order,
            AuditLog.ACTION_TRANSITION,
            actor=actor,
            changes={"from": from_state, "to": to_state},
            note=note,
        )
        self.event(to_state, TIMELINE[to_state], actor=actor, note=note)
        self._emit_status(from_state, actor)
        return True

    def _products(self):
        items = list(self.order.items.all())
        return items, prefetch_products([i for i in items if i.content_type_id])

    # generic
    @transaction.atomic
    def transition(self, to_state, actor="system", note=""):
        """Staff-facing transition that routes to the proper business method."""
        self._lock()
        if to_state == Order.STATUS_CONFIRMED:
            return self.confirm(actor=actor)
        if to_state == Order.STATUS_CANCELLED:
            return self.cancel(actor=actor, reason=note)
        if to_state in (Order.STATUS_REFUNDED, Order.STATUS_PARTIALLY_REFUNDED):
            raise InvalidOrderTransitionError("Create and process a refund instead.")
        self._set_status(to_state, actor=actor, note=note)
        if to_state == Order.STATUS_DELIVERED:
            hooks.run("order.delivered", order=self.order)
        return self.order

    @transaction.atomic
    def confirm(self, actor="system"):
        """Commit the order (paid, pay-on-delivery accepted, or staff decision). Idempotent."""
        order = self._lock()
        if order.status != Order.STATUS_PENDING:
            return order
        if is_installed("flexcommerce_inventory"):
            from flexcommerce_inventory.services import confirm_order

            confirm_order(order.order_number)
        order.payment_due_at = None
        self._save("payment_due_at")
        self._set_status(Order.STATUS_CONFIRMED, actor=actor)
        hooks.run("order.confirmed", order=order)
        return order

    @transaction.atomic
    def mark_paid(self, amount=None, reference="", provider="", actor="system"):
        """Record a (full or partial) payment. Idempotent per reference."""
        order = self._lock()
        if reference and order.payment_reference == reference and order.is_paid:
            return order
        amount = round_price(to_decimal(amount if amount is not None else order.balance_due))
        if amount <= 0:
            return order
        order.amount_paid = round_price(order.amount_paid + amount)
        order.payment_reference = reference or order.payment_reference
        order.payment_provider = provider or order.payment_provider
        fields = ["amount_paid", "payment_reference", "payment_provider"]
        if order.amount_paid >= order.grand_total:
            order.payment_status = Order.PAYMENT_PAID
            order.paid_at = order.paid_at or timezone.now()
            fields += ["payment_status", "paid_at"]
        self._save(*fields)
        AuditLog.log(
            order,
            AuditLog.ACTION_UPDATE,
            actor=actor,
            changes={"paid": str(amount), "reference": reference},
        )
        self.event("payment_received", f"Payment of {order.currency} {amount:,.2f} received.", actor=actor)
        emit(
            "order.paid",
            signal=signals.order_paid,
            sender=Order,
            payload=order_payload(order),
            order=order,
            amount=amount,
            reference=reference,
        )
        hooks.run("order.paid", order=order, amount=amount, reference=reference)
        if order.status == Order.STATUS_CANCELLED:
            # Money arrived after the order was cancelled: it must go back.
            self.create_refund(amount, "Payment received after the order was cancelled.", actor="system")
        elif order.status == Order.STATUS_PENDING and order.payment_status == Order.PAYMENT_PAID:
            self.confirm(actor=actor)
        return order

    @transaction.atomic
    def mark_payment_failed(self, reason="", actor="system"):
        order = self._lock()
        if order.is_paid or order.status == Order.STATUS_CANCELLED:
            return order
        order.payment_status = Order.PAYMENT_FAILED
        self._save("payment_status")
        self.event(
            "payment_failed",
            "Payment was not successful. You can try again.",
            actor=actor,
            reason=reason,
        )
        emit(
            "payment.failed",
            signal=signals.order_payment_failed,
            sender=Order,
            payload=order_payload(order),
            order=order,
            reason=reason,
        )
        return order

    @transaction.atomic
    def cancel(self, actor="system", reason="", by_customer=False):
        order = self._lock()
        if by_customer and order.status not in orders_setting("CUSTOMER_CANCELLABLE_STATUSES"):
            raise OrderCancellationError(extra={"status": order.status})
        if not OrderStateMachine(order).can_transition(Order.STATUS_CANCELLED):
            raise OrderCancellationError(extra={"status": order.status})
        stock_committed = order.status != Order.STATUS_PENDING
        order.cancellation_reason = (reason or "")[:255]
        self._save("cancellation_reason")
        if is_installed("flexcommerce_inventory"):
            from flexcommerce_inventory.services import release_order, return_to_stock

            release_order(order.order_number)
            if stock_committed:
                items, products = self._products()
                for item in items:
                    product = products.get((item.content_type_id, item.object_id))
                    if product is not None:
                        return_to_stock(
                            product,
                            item.quantity,
                            reference=order.order_number,
                            note="Order cancelled",
                        )
        hooks.run("order.cancelled", order=order, actor=actor)
        self._set_status(Order.STATUS_CANCELLED, actor=actor, note=reason)
        if order.refundable_amount > 0:
            refund = self.create_refund(order.refundable_amount, reason or "Order cancelled", actor=actor)
            if orders_setting("AUTO_PROCESS_CANCELLATION_REFUNDS"):
                self.process_refund(refund, actor=actor)
        return order

    # ── fulfilment ───────────────────────────────────────────────────────────
    def _normalise_items(self, items, vendor_id=None, field="quantity_shipped"):
        order_items = {str(i.pk): i for i in self.order.items.all()}
        if items is None:
            items = [
                {"order_item_id": pk, "quantity": i.quantity - getattr(i, field)}
                for pk, i in order_items.items()
                if (vendor_id is None or str(i.vendor_id) == str(vendor_id)) and i.quantity > getattr(i, field)
            ]
        totals = defaultdict(int)
        for entry in items:
            pk = str(entry.get("order_item_id", ""))
            qty = int(entry.get("quantity", 0))
            if pk not in order_items:
                raise OrderError("Unknown order item.", code="invalid_order_item", extra={"order_item_id": pk})
            if qty <= 0:
                raise OrderError("Quantity must be positive.", code="invalid_quantity")
            if vendor_id is not None and str(order_items[pk].vendor_id) != str(vendor_id):
                raise OrderError("Item belongs to another vendor.", code="invalid_order_item")
            totals[pk] += qty
        if not totals:
            raise OrderError("Nothing to ship.", code="nothing_to_ship")
        return order_items, totals

    @transaction.atomic
    def create_shipment(
        self,
        items=None,
        carrier="",
        tracking_number="",
        tracking_url="",
        estimated_delivery=None,
        vendor_id=None,
        actor="system",
    ):
        order = self._lock()
        if order.status not in (
            Order.STATUS_CONFIRMED,
            Order.STATUS_PROCESSING,
            Order.STATUS_PARTIALLY_SHIPPED,
        ):
            raise OrderError(
                "This order cannot be shipped in its current state.",
                code="not_shippable",
                extra={"status": order.status},
            )
        order_items, totals = self._normalise_items(items, vendor_id)
        for pk, qty in totals.items():
            item = order_items[pk]
            if qty > item.quantity - item.quantity_shipped:
                raise OrderError(
                    "Cannot ship more than was ordered.",
                    code="over_shipment",
                    extra={"order_item_id": pk, "remaining": item.quantity - item.quantity_shipped},
                )
        now = timezone.now()
        shipment = Shipment.objects.create(
            order=order,
            carrier=carrier,
            tracking_number=tracking_number,
            tracking_url=tracking_url,
            estimated_delivery=estimated_delivery,
            status=Shipment.STATUS_DISPATCHED,
            shipped_at=now,
            items=[{"order_item_id": pk, "quantity": qty} for pk, qty in totals.items()],
            vendor_id=vendor_id,
        )
        ShipmentEvent.objects.create(
            shipment=shipment,
            status=Shipment.STATUS_DISPATCHED,
            description="Shipment dispatched.",
            occurred_at=now,
        )
        for pk, qty in totals.items():
            OrderItem.objects.filter(pk=pk).update(quantity_shipped=F("quantity_shipped") + qty)
        remaining = sum(i.quantity - i.quantity_shipped for i in order.items.all())
        message = f"Shipped with {carrier}" + (f" (tracking {tracking_number})." if tracking_number else ".")
        self.event("shipment_created", message, actor=actor, shipment_id=str(shipment.pk))
        emit(
            "shipment.created",
            signal=signals.shipment_created,
            sender=Shipment,
            payload={
                **order_payload(order),
                "shipment_id": str(shipment.pk),
                "carrier": carrier,
                "tracking_number": tracking_number,
                "tracking_url": tracking_url,
            },
            shipment=shipment,
            order=order,
        )
        self._set_status(Order.STATUS_SHIPPED if remaining == 0 else Order.STATUS_PARTIALLY_SHIPPED, actor=actor)
        return shipment

    @transaction.atomic
    def update_shipment(self, shipment, status, description="", location="", occurred_at=None, actor="system"):
        order = self._lock()
        if shipment.order_id != order.pk:
            raise OrderError("Shipment does not belong to this order.")
        now = timezone.now()
        event = ShipmentEvent.objects.create(
            shipment=shipment,
            status=status,
            description=description,
            location=location,
            occurred_at=occurred_at or now,
        )
        shipment.status = status
        fields = ["status", "updated_at"]
        if status == Shipment.STATUS_DELIVERED:
            shipment.delivered_at = occurred_at or now
            fields.append("delivered_at")
        shipment.save(update_fields=fields)
        label = dict(Shipment.STATUS_CHOICES)[status]
        self.event(
            "shipment_updated",
            f"{label}{f' — {location}' if location else ''}.",
            actor=actor,
            shipment_id=str(shipment.pk),
            status=status,
        )
        emit(
            "shipment.updated",
            signal=signals.shipment_updated,
            sender=Shipment,
            payload={
                **order_payload(order),
                "shipment_id": str(shipment.pk),
                "shipment_status": status,
            },
            shipment=shipment,
            order=order,
            event=event,
        )
        if status == Shipment.STATUS_DELIVERED:
            all_shipped = all(i.quantity_shipped >= i.quantity for i in order.items.all())
            all_delivered = not order.shipments.exclude(status=Shipment.STATUS_DELIVERED).exists()
            if all_shipped and all_delivered and order.status in (Order.STATUS_SHIPPED, Order.STATUS_PARTIALLY_SHIPPED):
                self._set_status(Order.STATUS_DELIVERED, actor=actor)
                hooks.run("order.delivered", order=order)
        return event

    @transaction.atomic
    def mark_delivered(self, actor="system"):
        """Manual delivery confirmation (e.g. rider confirmed pay-on-delivery drop-off)."""
        order = self._lock()
        for shipment in order.shipments.exclude(status=Shipment.STATUS_DELIVERED):
            self.update_shipment(shipment, Shipment.STATUS_DELIVERED, actor=actor)
        order.refresh_from_db()
        if order.status != Order.STATUS_DELIVERED:
            self._set_status(Order.STATUS_DELIVERED, actor=actor)
            hooks.run("order.delivered", order=order)
        return order

    # ── refunds ──────────────────────────────────────────────────────────────
    def _pending_refunds(self):
        return (
            self.order.refunds.filter(status__in=[Refund.STATUS_REQUESTED, Refund.STATUS_APPROVED]).aggregate(
                total=Sum("amount")
            )["total"]
            or ZERO
        )

    @transaction.atomic
    def create_refund(
        self,
        amount,
        reason,
        items=None,
        method=Refund.METHOD_ORIGINAL,
        actor="system",
        return_request=None,
        status=Refund.STATUS_REQUESTED,
    ):
        order = self._lock()
        amount = round_price(to_decimal(amount))
        available = order.refundable_amount - self._pending_refunds()
        if amount <= 0:
            raise RefundError("Refund amount must be positive.")
        if amount > available:
            raise RefundError(
                "Refund exceeds the refundable amount.",
                extra={"refundable": str(max(ZERO, available))},
            )
        refund = Refund.objects.create(
            order=order,
            amount=amount,
            reason=reason or "Refund",
            items=items or [],
            method=method,
            return_request=return_request,
            status=status,
        )
        self.event(
            "refund_requested",
            f"Refund of {order.currency} {amount:,.2f} initiated.",
            actor=actor,
            refund_id=str(refund.pk),
        )
        emit(
            "refund.requested",
            signal=signals.refund_requested,
            sender=Refund,
            payload={**order_payload(order), "refund_id": str(refund.pk), "amount": str(amount)},
            refund=refund,
            order=order,
        )
        return refund

    @transaction.atomic
    def process_refund(self, refund, actor="system", processed_by=None):
        """Pay the refund out (gateway / wallet via the ``refund.process`` hook, else manual)."""
        order = self._lock()
        refund = Refund.objects.select_for_update().get(pk=refund.pk, order=order)
        if refund.status not in (Refund.STATUS_REQUESTED, Refund.STATUS_APPROVED):
            raise RefundError("This refund has already been handled.", extra={"status": refund.status})
        if refund.amount > order.refundable_amount:
            raise RefundError(
                "Refund exceeds the amount paid.",
                extra={"refundable": str(order.refundable_amount)},
            )
        result = {
            "success": True,
            "reference": refund.reference or f"MANUAL-{refund.pk.hex[:10]}",
            "error": "",
        }
        if refund.method != Refund.METHOD_MANUAL:
            for outcome in hooks.run("refund.process", refund=refund, order=order):
                if outcome:
                    result = outcome
                    break
        now = timezone.now()
        if not result.get("success"):
            refund.status = Refund.STATUS_FAILED
            refund.failure_reason = str(result.get("error", ""))[:500]
            refund.save(update_fields=["status", "failure_reason", "updated_at"])
            emit(
                "refund.failed",
                signal=signals.refund_failed,
                sender=Refund,
                payload={**order_payload(order), "refund_id": str(refund.pk)},
                refund=refund,
                order=order,
            )
            return refund
        refund.status = Refund.STATUS_PROCESSED
        refund.reference = str(result.get("reference", ""))[:200]
        refund.processed_at = now
        refund.processed_by = processed_by if getattr(processed_by, "is_authenticated", False) else None
        refund.save(update_fields=["status", "reference", "processed_at", "processed_by", "updated_at"])
        order.amount_refunded = round_price(order.amount_refunded + refund.amount)
        fully = order.amount_refunded >= order.amount_paid
        order.payment_status = Order.PAYMENT_REFUNDED if fully else Order.PAYMENT_PARTIALLY_REFUNDED
        self._save("amount_refunded", "payment_status")
        AuditLog.log(refund, AuditLog.ACTION_UPDATE, actor=actor, changes={"processed": str(refund.amount)})
        self.event(
            "refund_processed",
            f"Refund of {order.currency} {refund.amount:,.2f} completed.",
            actor=actor,
            refund_id=str(refund.pk),
        )
        emit(
            "refund.processed",
            signal=signals.refund_processed,
            sender=Refund,
            payload={
                **order_payload(order),
                "refund_id": str(refund.pk),
                "amount": str(refund.amount),
            },
            refund=refund,
            order=order,
        )
        if refund.return_request_id:
            ReturnRequest.objects.filter(pk=refund.return_request_id).update(status=ReturnRequest.STATUS_REFUNDED)
        if order.status in (Order.STATUS_DELIVERED, Order.STATUS_PARTIALLY_REFUNDED):
            self._set_status(Order.STATUS_REFUNDED if fully else Order.STATUS_PARTIALLY_REFUNDED, actor=actor)
        return refund

    @transaction.atomic
    def reject_refund(self, refund, reason="", actor="system"):
        refund = Refund.objects.select_for_update().get(pk=refund.pk, order=self.order)
        if refund.status not in (
            Refund.STATUS_REQUESTED,
            Refund.STATUS_APPROVED,
            Refund.STATUS_FAILED,
        ):
            raise RefundError("This refund has already been handled.")
        refund.status = Refund.STATUS_REJECTED
        refund.failure_reason = reason[:500]
        refund.save(update_fields=["status", "failure_reason", "updated_at"])
        self.event(
            "refund_rejected",
            "A refund request was declined.",
            actor=actor,
            visible=False,
            reason=reason,
        )
        return refund

    # ── returns ──────────────────────────────────────────────────────────────
    @transaction.atomic
    def request_return(
        self,
        items,
        reason_code="other",
        reason="",
        image_urls=None,
        refund_method=Refund.METHOD_ORIGINAL,
        user=None,
        actor="customer",
    ):
        order = self._lock()
        window = orders_setting("RETURN_WINDOW_DAYS")
        if not window:
            raise ReturnError("Returns are not accepted.", code="returns_disabled")
        if order.status not in (Order.STATUS_DELIVERED, Order.STATUS_PARTIALLY_REFUNDED) or not order.delivered_at:
            raise ReturnError("Only delivered orders can be returned.", code="not_delivered")
        if timezone.now() > order.delivered_at + timedelta(days=window):
            raise ReturnError(f"The {window}-day return window has closed.", code="return_window_closed")
        order_items, totals = self._normalise_items(items, field="quantity_returned")
        pending = defaultdict(int)
        for rr in order.returns.filter(status__in=[ReturnRequest.STATUS_PENDING, ReturnRequest.STATUS_APPROVED]):
            for entry in rr.items:
                pending[str(entry["order_item_id"])] += int(entry["quantity"])
        for pk, qty in totals.items():
            item = order_items[pk]
            allowed = item.quantity_shipped - item.quantity_returned - pending[pk]
            if qty > allowed:
                raise ReturnError(
                    "Cannot return more than was delivered.",
                    code="over_return",
                    extra={"order_item_id": pk, "returnable": max(0, allowed)},
                )
        rr = ReturnRequest.objects.create(
            order=order,
            user=user if getattr(user, "is_authenticated", False) else None,
            reason_code=reason_code,
            reason=reason or "",
            image_urls=image_urls or [],
            refund_method=refund_method,
            items=[{"order_item_id": pk, "quantity": q} for pk, q in totals.items()],
        )
        self.event(
            "return_requested",
            "We received your return request.",
            actor=actor,
            return_id=str(rr.pk),
        )
        emit(
            "return.requested",
            signal=signals.return_requested,
            sender=ReturnRequest,
            payload={**order_payload(order), "return_id": str(rr.pk)},
            return_request=rr,
            order=order,
        )
        return rr

    def _return_update(self, rr, status, message, actor, note="", approved_by=None):
        rr.status = status
        rr.staff_note = note or rr.staff_note
        fields = ["status", "staff_note", "updated_at"]
        if approved_by is not None:
            rr.approved_by = approved_by
            fields.append("approved_by")
        rr.save(update_fields=fields)
        self.event("return_updated", message, actor=actor, return_id=str(rr.pk), status=status)
        emit(
            "return.updated",
            signal=signals.return_updated,
            sender=ReturnRequest,
            payload={**order_payload(self.order), "return_id": str(rr.pk), "return_status": status},
            return_request=rr,
            order=self.order,
        )
        return rr

    @transaction.atomic
    def approve_return(self, rr, actor="system", note="", approved_by=None):
        self._lock()
        if rr.status != ReturnRequest.STATUS_PENDING:
            raise ReturnError("Only pending returns can be approved.")
        user = approved_by if getattr(approved_by, "is_authenticated", False) else None
        return self._return_update(rr, ReturnRequest.STATUS_APPROVED, "Your return was approved.", actor, note, user)

    @transaction.atomic
    def reject_return(self, rr, actor="system", note=""):
        self._lock()
        if rr.status not in (ReturnRequest.STATUS_PENDING, ReturnRequest.STATUS_APPROVED):
            raise ReturnError("This return can no longer be rejected.")
        return self._return_update(rr, ReturnRequest.STATUS_REJECTED, "Your return request was declined.", actor, note)

    @transaction.atomic
    def receive_return(self, rr, actor="system", restock=True, refund=True, note=""):
        """Item arrived back: restock it and create the refund for the returned items."""
        order = self._lock()
        if rr.status != ReturnRequest.STATUS_APPROVED:
            raise ReturnError("Approve the return before receiving it.")
        items = {str(i.pk): i for i in order.items.all()}
        amount = ZERO
        products = prefetch_products(list(items.values())) if restock else {}
        for entry in rr.items:
            item = items[str(entry["order_item_id"])]
            qty = int(entry["quantity"])
            OrderItem.objects.filter(pk=item.pk).update(quantity_returned=F("quantity_returned") + qty)
            amount += round_price(item.unit_paid * qty)
            product = products.get((item.content_type_id, item.object_id))
            if restock and product is not None and is_installed("flexcommerce_inventory"):
                from flexcommerce_inventory.services import return_to_stock

                return_to_stock(product, qty, reference=order.order_number, note=f"Return {rr.pk}")
        rr.received_at = timezone.now()
        rr.save(update_fields=["received_at", "updated_at"])
        self._return_update(rr, ReturnRequest.STATUS_RECEIVED, "We received your returned item(s).", actor, note)
        refund_obj = None
        amount = min(amount, order.refundable_amount - self._pending_refunds())
        if refund and amount > 0:
            refund_obj = self.create_refund(
                amount,
                f"Return {rr.get_reason_code_display()}",
                items=rr.items,
                method=rr.refund_method,
                actor=actor,
                return_request=rr,
                status=Refund.STATUS_APPROVED,
            )
        return rr, refund_obj


# ── Queries & jobs ───────────────────────────────────────────────────────────


def find_guest_order(order_number, email):
    if not order_number or not email:
        return None
    return Order.objects.filter(order_number=order_number.strip(), email__iexact=email.strip()).first()


def expire_unpaid_orders(limit=500):
    """Job: cancel orders whose payment deadline passed (releases stock and coupons)."""
    due = Order.objects.filter(
        status=Order.STATUS_PENDING,
        payment_status__in=[Order.PAYMENT_PENDING, Order.PAYMENT_FAILED],
        payment_due_at__lt=timezone.now(),
    ).order_by("payment_due_at")[:limit]
    cancelled = 0
    for order in due:
        try:
            OrderService(order).cancel(actor="system", reason="Payment was not received in time.")
            cancelled += 1
        except Exception:
            logger.exception("Could not expire order %s", order.order_number)
    return {"cancelled": cancelled}
