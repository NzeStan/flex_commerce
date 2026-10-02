"""Record ``AnalyticsEvent`` rows from domain signals (connected only for installed apps)."""

import logging
from decimal import Decimal

from flexcommerce_core.utils.products import is_installed

logger = logging.getLogger("flexcommerce.analytics")


def _record(
    event_type,
    user=None,
    session_key="",
    amount=Decimal("0"),
    currency="NGN",
    reference="",
    vendor_id=None,
    meta=None,
):
    from .models import AnalyticsEvent

    try:
        AnalyticsEvent.objects.create(
            event_type=event_type,
            user=user if getattr(user, "pk", None) else None,
            session_key=session_key or "",
            amount=amount or Decimal("0"),
            currency=currency or "NGN",
            reference=str(reference)[:100],
            vendor_id=vendor_id,
            meta=meta or {},
        )
    except Exception:  # analytics must never break commerce
        logger.exception("Analytics record error (%s)", event_type)


def _order_event(event_type):
    def handler(sender, order, **kwargs):
        _record(
            event_type,
            user=order.user,
            session_key=order.session_key,
            amount=order.grand_total,
            currency=order.currency,
            reference=order.order_number,
            meta={"payment_method": order.payment_method, "coupon": order.coupon_code},
        )

    handler.__name__ = f"on_{event_type.replace('.', '_')}"
    return handler


on_order_created = _order_event("order.created")
on_order_confirmed = _order_event("order.confirmed")
on_order_paid = _order_event("order.paid")
on_order_cancelled = _order_event("order.cancelled")
on_order_refunded = _order_event("order.refunded")


def on_refund_processed(sender, refund, order, **kwargs):
    _record(
        "refund.processed",
        user=order.user,
        amount=refund.amount,
        currency=order.currency,
        reference=order.order_number,
    )


def on_cart_abandoned(sender, cart, **kwargs):
    _record(
        "cart.abandoned",
        user=cart.user,
        session_key=cart.session_key,
        amount=cart.total_amount,
        currency=cart.currency,
        reference=str(cart.pk),
        meta={"item_count": cart.items_count},
    )


def on_cart_recovered(sender, cart, order, **kwargs):
    _record(
        "cart.recovered",
        user=cart.user,
        amount=order.grand_total,
        currency=order.currency,
        reference=order.order_number,
    )


def on_coupon_applied(sender, cart, code, discount, **kwargs):
    _record(
        "coupon.used",
        user=cart.user,
        session_key=cart.session_key,
        amount=cart.discount_amount,
        currency=cart.currency,
        reference=code,
        meta={"coupon_code": code},
    )


def on_product_viewed(sender, product, user=None, **kwargs):
    _record(
        "product.viewed",
        user=user,
        reference=str(product.pk),
        vendor_id=getattr(product, "vendor_id", None),
        meta={"product_type": product._meta.label_lower},
    )


def on_review_submitted(sender, review, **kwargs):
    _record(
        "review.submitted",
        user=review.user,
        reference=str(review.object_id),
        meta={"rating": review.rating},
    )


def connect_all_signals():
    if is_installed("flexcommerce_orders"):
        from flexcommerce_orders import signals as s

        for signal, handler in [
            (s.order_created, on_order_created),
            (s.order_confirmed, on_order_confirmed),
            (s.order_paid, on_order_paid),
            (s.order_cancelled, on_order_cancelled),
            (s.order_refunded, on_order_refunded),
            (s.refund_processed, on_refund_processed),
        ]:
            signal.connect(handler, dispatch_uid=f"analytics:{handler.__name__}")
    if is_installed("flexcommerce_cart"):
        from flexcommerce_cart import signals as s

        s.cart_abandoned.connect(on_cart_abandoned, dispatch_uid="analytics:cart_abandoned")
        s.cart_recovered.connect(on_cart_recovered, dispatch_uid="analytics:cart_recovered")
        s.coupon_applied.connect(on_coupon_applied, dispatch_uid="analytics:coupon_applied")
    if is_installed("flexcommerce_engagement"):
        from flexcommerce_engagement import signals as s

        s.product_viewed.connect(on_product_viewed, dispatch_uid="analytics:product_viewed")
        s.review_submitted.connect(on_review_submitted, dispatch_uid="analytics:review_submitted")
