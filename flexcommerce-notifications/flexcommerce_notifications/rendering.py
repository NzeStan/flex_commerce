"""
Message rendering.

Every built-in event has a sensible default message below. Override any of them
without code via the admin (``NotificationTemplate``) or override the HTML email
layout by providing ``flexcommerce/notifications/email_base.html`` in your
project's templates.
"""

from django.template import Context, Engine
from django.template.loader import render_to_string
from django.utils.html import linebreaks

_engine = Engine(autoescape=False)
_html_engine = Engine(autoescape=True)

DEFAULT_MESSAGES = {
    "order.created": {
        "subject": "Order {{ order_number }} received",
        "body": "Hi {{ customer_name }}, we have received your order {{ order_number }} "
        "({{ currency }} {{ grand_total }}).{% if payment_method == 'pay_on_delivery' %} Please have the "
        "amount ready on delivery.{% endif %} Track it here: {{ order_url }}",
        "sms": "{{ store_name }}: order {{ order_number }} received. Total {{ currency }} {{ grand_total }}.",
    },
    "order.confirmed": {
        "subject": "Order {{ order_number }} confirmed",
        "body": "Hi {{ customer_name }}, your order {{ order_number }} is confirmed and is being prepared.",
        "sms": "{{ store_name }}: order {{ order_number }} confirmed.",
    },
    "order.paid": {
        "subject": "Payment received for order {{ order_number }}",
        "body": "Hi {{ customer_name }}, we received your payment of {{ currency }} {{ amount }} for order "
        "{{ order_number }}. Thank you!",
        "sms": "{{ store_name }}: payment of {{ currency }} {{ amount }} received for {{ order_number }}.",
    },
    "payment.failed": {
        "subject": "Payment for order {{ order_number }} was not successful",
        "body": "Hi {{ customer_name }}, your payment for order {{ order_number }} did not go through. "
        "You can try again here: {{ order_url }}",
        "sms": "{{ store_name }}: payment for {{ order_number }} failed. Please try again.",
    },
    "order.shipped": {
        "subject": "Order {{ order_number }} is on its way",
        "body": "Hi {{ customer_name }}, your order {{ order_number }} has been shipped"
        "{% if carrier %} with {{ carrier }}{% endif %}.{% if tracking_number %} Tracking number: "
        "{{ tracking_number }}.{% endif %}{% if tracking_url %} Track: {{ tracking_url }}{% endif %}",
        "sms": "{{ store_name }}: {{ order_number }} shipped{% if tracking_number %}, tracking {{ tracking_number }}"
        "{% endif %}.",
    },
    "shipment.out_for_delivery": {
        "subject": "Order {{ order_number }} is out for delivery",
        "body": "Hi {{ customer_name }}, your order {{ order_number }} is out for delivery today.",
        "sms": "{{ store_name }}: {{ order_number }} is out for delivery today. Keep your phone close.",
    },
    "order.delivered": {
        "subject": "Order {{ order_number }} delivered",
        "body": "Hi {{ customer_name }}, your order {{ order_number }} has been delivered. We hope you love it! "
        "Tell others what you think by leaving a review.",
        "sms": "{{ store_name }}: {{ order_number }} delivered. Thank you for shopping with us!",
    },
    "order.cancelled": {
        "subject": "Order {{ order_number }} cancelled",
        "body": "Hi {{ customer_name }}, your order {{ order_number }} was cancelled."
        "{% if reason %} Reason: {{ reason }}.{% endif %}{% if refundable %} Any payment will be "
        "refunded.{% endif %}",
        "sms": "{{ store_name }}: order {{ order_number }} was cancelled.",
    },
    "refund.processed": {
        "subject": "Refund for order {{ order_number }}",
        "body": "Hi {{ customer_name }}, we have refunded {{ currency }} {{ refund_amount }} for order "
        "{{ order_number }}.",
        "sms": "{{ store_name }}: refund of {{ currency }} {{ refund_amount }} processed for {{ order_number }}.",
    },
    "return.updated": {
        "subject": "Update on your return for order {{ order_number }}",
        "body": "Hi {{ customer_name }}, your return request for order {{ order_number }} is now: {{ return_status }}.",
        "sms": "{{ store_name }}: return for {{ order_number }} is now {{ return_status }}.",
    },
    "cart.abandoned": {
        "subject": "You left something in your cart",
        "body": "Hi {{ customer_name }}, you still have {{ item_count }} item(s) worth {{ currency }} {{ cart_total }} "
        "in your cart. Complete your order before they sell out!",
        "sms": "{{ store_name }}: your cart is waiting ({{ item_count }} items).",
    },
    "wishlist.price_drop": {
        "subject": "Price drop on {{ product_name }}",
        "body": "Good news {{ customer_name }}! {{ product_name }} dropped from {{ currency }} {{ old_price }} to "
        "{{ currency }} {{ new_price }}.",
        "sms": "{{ store_name }}: {{ product_name }} is now {{ currency }} {{ new_price }}.",
    },
    "inventory.back_in_stock": {
        "subject": "{{ product_name }} is back in stock",
        "body": "Hi, {{ product_name }} is available again. Get it before it sells out!",
        "sms": "{{ store_name }}: {{ product_name }} is back in stock.",
    },
    "question.answered": {
        "subject": "Your question was answered",
        "body": 'Your question "{{ question }}" has a new answer: {{ answer }}',
    },
    "vendor.approved": {
        "subject": "Your shop {{ vendor_name }} is approved",
        "body": "Congratulations! {{ vendor_name }} can now list products and receive orders.",
    },
    "vendor.new_order": {
        "subject": "New order {{ order_number }}",
        "body": "You have a new order {{ order_number }} with {{ item_count }} item(s) worth {{ currency }} "
        "{{ amount }}. Please prepare it for shipping.",
        "sms": "{{ store_name }}: new order {{ order_number }} for your shop.",
    },
    "payout.updated": {
        "subject": "Payout {{ status }}: {{ currency }} {{ amount }}",
        "body": "Your payout of {{ currency }} {{ amount }} is {{ status }}.{% if reference %} Reference: "
        "{{ reference }}.{% endif %}",
    },
    "wallet.credit": {
        "subject": "{{ currency }} {{ amount }} added to your wallet",
        "body": "{{ currency }} {{ amount }} was added to your wallet ({{ source }}). New balance: {{ currency }} "
        "{{ balance }}.",
    },
    # Staff alerts
    "staff.order_created": {
        "subject": "New order {{ order_number }} — {{ currency }} {{ grand_total }}",
        "body": "New order {{ order_number }} from {{ customer_email }} ({{ payment_method }}).",
    },
    "inventory.low_stock": {
        "subject": "Low stock: {{ sku }}",
        "body": "{{ sku }} is running low: {{ available }} available (on hand {{ on_hand }}).",
    },
    "inventory.out_of_stock": {
        "subject": "Out of stock: {{ sku }}",
        "body": "{{ sku }} is out of stock.",
    },
    "review.submitted": {
        "subject": "New review to moderate ({{ rating }}★)",
        "body": 'A new {{ rating }}-star review "{{ title }}" is waiting for moderation.',
    },
    "return.requested": {
        "subject": "Return requested for order {{ order_number }}",
        "body": "A customer requested a return for order {{ order_number }}.",
    },
    "vendor.applied": {
        "subject": "New seller application: {{ vendor_name }}",
        "body": "{{ vendor_name }} applied to sell on the marketplace and is waiting for review.",
    },
}


def render_string(template_string, context, autoescape=False):
    if not template_string:
        return ""
    engine = _html_engine if autoescape else _engine
    return engine.from_string(template_string).render(Context(context, autoescape=autoescape)).strip()


def render_message(event, channel, context):
    """Return ``{"subject", "body", "html_body"}`` for ``event`` on ``channel``."""
    from .models import NotificationTemplate

    template = NotificationTemplate.objects.filter(event=event, channel=channel, is_active=True).first()
    if template is not None:
        rendered = template.render(context)
    else:
        defaults = DEFAULT_MESSAGES.get(event, {})
        subject = defaults.get("subject") or context.get("subject") or event.replace(".", " ").replace("_", " ").title()
        body = defaults.get("sms") if channel == "sms" and defaults.get("sms") else defaults.get("body", "")
        rendered = {
            "subject": render_string(subject, context),
            "body": render_string(body or context.get("body", ""), context),
            "html_body": "",
        }
    if channel == "email" and not rendered["html_body"]:
        rendered["html_body"] = render_to_string(
            "flexcommerce/notifications/email_base.html",
            {**context, "subject": rendered["subject"], "body_html": linebreaks(rendered["body"])},
        )
    if channel == "sms":
        rendered["body"] = rendered["body"][:612]  # 4 SMS pages max
    return rendered
