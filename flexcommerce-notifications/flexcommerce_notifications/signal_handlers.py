"""
Signal wiring: FlexCommerce domain events → customer / staff / vendor notifications.

Connected in ``AppConfig.ready`` only for apps that are installed, so every
package stays optional. All receivers run after commit and are isolated (a
failing receiver never affects the request or other receivers).

Disable or re-route any event in settings::

    FLEXCOMMERCE = {"NOTIFICATION_EVENTS": {"cart.abandoned": False, "order.shipped": ["sms", "push"]}}
"""

import logging

from django.contrib.auth import get_user_model

from flexcommerce_core.utils.products import is_installed, product_name

from .conf import notifications_setting
from .dispatcher import Recipient, notify

logger = logging.getLogger("flexcommerce.notifications")


# ── helpers ──────────────────────────────────────────────────────────────────


def order_recipient(order):
    phone = order.phone or (order.shipping_address or {}).get("phone", "")
    return Recipient(user=order.user, email=order.customer_email, phone=phone, name=order.customer_name)


def order_context(order, **extra):
    return {
        "order_number": order.order_number,
        "grand_total": f"{order.grand_total:,.2f}",
        "currency": order.currency,
        "payment_method": order.payment_method,
        "customer_name": order.customer_name,
        "customer_email": order.customer_email,
        **extra,
    }


def staff_recipients():
    emails = notifications_setting("STAFF_NOTIFICATION_EMAILS") or []
    if emails:
        return [Recipient(email=e) for e in emails]
    User = get_user_model()
    return [Recipient(user=u) for u in User.objects.filter(is_staff=True, is_active=True).exclude(email="")]


def notify_staff(event, context, dedupe=None):
    if event not in notifications_setting("STAFF_NOTIFICATION_EVENTS"):
        return []
    return notify(event, context, staff_recipients(), channels=["email", "in_app"], dedupe=dedupe)


def vendor_owner(vendor):
    return Recipient(
        user=vendor.owner,
        email=vendor.email or vendor.owner.email,
        phone=vendor.phone,
        name=vendor.name,
    )


# ── orders ───────────────────────────────────────────────────────────────────


def on_order_created(sender, order, **kwargs):
    notify(
        "order.created",
        order_context(order),
        [order_recipient(order)],
        dedupe=f"order.created:{order.pk}",
    )
    notify_staff("staff.order_created", order_context(order), dedupe=f"staff.order_created:{order.pk}")


def on_order_paid(sender, order, amount=None, reference="", **kwargs):
    notify(
        "order.paid",
        order_context(order, amount=f"{amount or order.amount_paid:,.2f}"),
        [order_recipient(order)],
        dedupe=f"order.paid:{order.pk}:{reference}",
    )


def on_payment_failed(sender, order, reason="", **kwargs):
    notify("payment.failed", order_context(order, reason=reason), [order_recipient(order)])


def on_order_shipped(sender, order, **kwargs):
    shipment = order.shipments.order_by("-created_at").first()
    ctx = order_context(
        order,
        carrier=getattr(shipment, "carrier", ""),
        tracking_number=getattr(shipment, "tracking_number", ""),
        tracking_url=getattr(shipment, "tracking_url", ""),
    )
    notify(
        "order.shipped",
        ctx,
        [order_recipient(order)],
        dedupe=f"order.shipped:{order.pk}:{order.status}",
    )


def on_shipment_updated(sender, shipment, order, **kwargs):
    if shipment.status == "out_for_delivery":
        notify(
            "shipment.out_for_delivery",
            order_context(order),
            [order_recipient(order)],
            dedupe=f"shipment.ofd:{shipment.pk}",
        )


def on_order_delivered(sender, order, **kwargs):
    notify(
        "order.delivered",
        order_context(order),
        [order_recipient(order)],
        dedupe=f"order.delivered:{order.pk}",
    )


def on_order_cancelled(sender, order, **kwargs):
    ctx = order_context(order, reason=order.cancellation_reason, refundable=order.amount_paid > 0)
    notify("order.cancelled", ctx, [order_recipient(order)], dedupe=f"order.cancelled:{order.pk}")


def on_refund_processed(sender, refund, order, **kwargs):
    ctx = order_context(order, refund_amount=f"{refund.amount:,.2f}")
    notify("refund.processed", ctx, [order_recipient(order)], dedupe=f"refund.processed:{refund.pk}")


def on_return_requested(sender, return_request, order, **kwargs):
    notify_staff("return.requested", order_context(order), dedupe=f"return.requested:{return_request.pk}")


def on_return_updated(sender, return_request, order, **kwargs):
    ctx = order_context(order, return_status=return_request.get_status_display())
    notify(
        "return.updated",
        ctx,
        [order_recipient(order)],
        dedupe=f"return:{return_request.pk}:{return_request.status}",
    )


# ── carts, wishlists, stock ──────────────────────────────────────────────────


def on_cart_abandoned(sender, cart, **kwargs):
    recipient = Recipient(user=cart.user, email=cart.email, phone=cart.phone)
    ctx = {
        "item_count": cart.items_count,
        "cart_total": f"{cart.total_amount:,.2f}",
        "currency": cart.currency,
        "customer_name": cart.user.get_full_name() if cart.user_id else "there",
    }
    notify("cart.abandoned", ctx, [recipient], dedupe=f"cart.abandoned:{cart.pk}")


def on_price_drop(sender, item, user, product, old_price, new_price, **kwargs):
    from flexcommerce_core.conf import fc_setting

    ctx = {
        "product_name": product_name(product),
        "old_price": f"{old_price:,.2f}",
        "new_price": f"{new_price:,.2f}",
        "currency": fc_setting("CURRENCY", "NGN"),
        "customer_name": user.get_full_name() or "there",
    }
    notify("wishlist.price_drop", ctx, [Recipient(user=user)], dedupe=f"price_drop:{item.pk}")


def on_back_in_stock(sender, item, alerts, **kwargs):
    from flexcommerce_inventory.services import mark_alerts_notified

    product = item.product
    ctx = {"product_name": product_name(product) if product is not None else item.sku}
    for alert in alerts:
        notify(
            "inventory.back_in_stock",
            ctx,
            [Recipient(user=alert.user, email=alert.email, phone=alert.phone)],
            dedupe=f"back_in_stock:{alert.pk}",
        )
    mark_alerts_notified(alerts)


def _stock_ctx(item):
    return {
        "sku": item.sku or str(item.object_id),
        "on_hand": item.on_hand,
        "available": item.available,
    }


def on_low_stock(sender, item, **kwargs):
    notify_staff("inventory.low_stock", _stock_ctx(item))


def on_out_of_stock(sender, item, **kwargs):
    notify_staff("inventory.out_of_stock", _stock_ctx(item))


# ── engagement ───────────────────────────────────────────────────────────────


def on_review_submitted(sender, review, **kwargs):
    if not review.is_approved:
        notify_staff(
            "review.submitted",
            {"rating": review.rating, "title": review.title},
            dedupe=f"review.submitted:{review.pk}",
        )


def on_question_answered(sender, question, answer, **kwargs):
    if answer.user_id == question.user_id:
        return
    notify(
        "question.answered",
        {"question": question.question[:200], "answer": answer.answer[:500]},
        [Recipient(user=question.user)],
        dedupe=f"answer:{answer.pk}",
    )


# ── marketplace & wallet ─────────────────────────────────────────────────────


def on_vendor_applied(sender, vendor, **kwargs):
    notify_staff("vendor.applied", {"vendor_name": vendor.name}, dedupe=f"vendor.applied:{vendor.pk}")


def on_vendor_approved(sender, vendor, **kwargs):
    notify(
        "vendor.approved",
        {"vendor_name": vendor.name},
        [vendor_owner(vendor)],
        dedupe=f"vendor.approved:{vendor.pk}",
    )


def on_vendor_order_created(sender, vendor_order, **kwargs):
    order = vendor_order.order
    count = sum(i.quantity for i in order.items.all() if i.vendor_id == vendor_order.vendor_id)
    ctx = {
        "order_number": order.order_number,
        "item_count": count,
        "currency": order.currency,
        "amount": f"{vendor_order.gross_amount:,.2f}",
        "order_url": "",
    }
    notify(
        "vendor.new_order",
        ctx,
        [vendor_owner(vendor_order.vendor)],
        dedupe=f"vendor_order:{vendor_order.pk}",
    )


def on_payout_updated(sender, payout, **kwargs):
    ctx = {
        "amount": f"{payout.amount:,.2f}",
        "currency": payout.currency,
        "status": payout.get_status_display(),
        "reference": payout.reference,
    }
    notify(
        "payout.updated",
        ctx,
        [vendor_owner(payout.vendor)],
        dedupe=f"payout:{payout.pk}:{payout.status}",
    )


def on_wallet_transaction(sender, transaction, wallet, **kwargs):
    if transaction.type != "credit" or transaction.source not in ("refund", "topup", "cashback"):
        return
    ctx = {
        "amount": f"{transaction.amount:,.2f}",
        "currency": wallet.currency,
        "source": transaction.get_source_display(),
        "balance": f"{transaction.balance_after:,.2f}",
    }
    notify("wallet.credit", ctx, [Recipient(user=wallet.user)], dedupe=f"wallet:{transaction.pk}")


# ── registration ─────────────────────────────────────────────────────────────


def _connect(signal, handler, name):
    signal.connect(handler, dispatch_uid=f"flexcommerce_notifications:{name}")


def connect_all_signals():
    if is_installed("flexcommerce_orders"):
        from flexcommerce_orders import signals as s

        for signal, handler in [
            (s.order_created, on_order_created),
            (s.order_paid, on_order_paid),
            (s.order_payment_failed, on_payment_failed),
            (s.order_shipped, on_order_shipped),
            (s.order_partially_shipped, on_order_shipped),
            (s.shipment_updated, on_shipment_updated),
            (s.order_delivered, on_order_delivered),
            (s.order_cancelled, on_order_cancelled),
            (s.refund_processed, on_refund_processed),
            (s.return_requested, on_return_requested),
            (s.return_updated, on_return_updated),
        ]:
            _connect(signal, handler, handler.__name__)
    if is_installed("flexcommerce_cart"):
        from flexcommerce_cart import signals as s

        _connect(s.cart_abandoned, on_cart_abandoned, "cart_abandoned")
    if is_installed("flexcommerce_inventory"):
        from flexcommerce_inventory import signals as s

        _connect(s.back_in_stock, on_back_in_stock, "back_in_stock")
        _connect(s.low_stock, on_low_stock, "low_stock")
        _connect(s.out_of_stock, on_out_of_stock, "out_of_stock")
    if is_installed("flexcommerce_engagement"):
        from flexcommerce_engagement import signals as s

        _connect(s.review_submitted, on_review_submitted, "review_submitted")
        _connect(s.question_answered, on_question_answered, "question_answered")
        _connect(s.wishlist_price_drop, on_price_drop, "price_drop")
    if is_installed("flexcommerce_marketplace"):
        from flexcommerce_marketplace import signals as s

        _connect(s.vendor_applied, on_vendor_applied, "vendor_applied")
        _connect(s.vendor_approved, on_vendor_approved, "vendor_approved")
        _connect(s.vendor_order_created, on_vendor_order_created, "vendor_order_created")
        _connect(s.payout_updated, on_payout_updated, "payout_updated")
    if is_installed("flexcommerce_payments"):
        from flexcommerce_payments import signals as s

        _connect(s.wallet_transaction, on_wallet_transaction, "wallet_transaction")
