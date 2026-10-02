# Events, hooks, webhooks & notifications

FlexCommerce apps never import each other's side effects. They talk through three mechanisms, each with a
clear job:

| Mechanism | When it runs | Can it stop the operation? | Use it for |
|---|---|---|---|
| **Hooks** (`flexcommerce_core.hooks`) | Inside the database transaction | Yes — raise to abort | Changing the business operation itself (split an order per vendor, claim flash-sale units, adjust a price) |
| **Signals** (`flexcommerce_core.events.emit`) | **After commit** | No | Side effects: emails, SMS, analytics, cache busting, your integrations |
| **Webhooks** (`WebhookEndpoint`) | After commit, async, retried | No | Other systems (ERP, CRM, warehouse, Slack) over HTTPS |

Because signals fire only after commit, nothing is ever sent for an order that was rolled back, and a failing
receiver is logged but never breaks the request or the other receivers.

## Signals

Connect in your app's `AppConfig.ready()`:

```python
from django.dispatch import receiver
from flexcommerce_orders.signals import order_confirmed

@receiver(order_confirmed)
def notify_warehouse(sender, order, from_state, actor, **kwargs):
    ...
```

| App | Signals (kwargs) |
|---|---|
| orders | `order_created`, `order_confirmed`, `order_paid(amount, reference)`, `order_payment_failed(reason)`, `order_processing`, `order_partially_shipped`, `order_shipped`, `order_delivered`, `order_cancelled`, `order_refunded`, `order_partially_refunded` — all with `order` (+ `from_state`, `actor` for transitions); `shipment_created(shipment, order)`, `shipment_updated(shipment, order, event)`, `refund_requested/processed/failed(refund, order)`, `return_requested/updated(return_request, order)` |
| cart | `item_added(cart, item, created)`, `item_updated`, `item_removed`, `cart_cleared`, `cart_merged(cart, merged_from)`, `cart_abandoned(cart)`, `cart_recovered(cart, order)`, `cart_expired`, `coupon_applied(cart, code, discount)`, `coupon_removed` |
| inventory | `stock_changed(item)`, `low_stock(item)`, `out_of_stock(item)`, `restocked(item, quantity)`, `back_in_stock(item, alerts)` |
| payments | `payment_succeeded(payment)`, `payment_failed(payment)`, `wallet_transaction(transaction, wallet)` |
| engagement | `review_submitted(review)`, `review_approved(review)`, `rating_changed(content_type, object_id, average, count)`, `wishlist_item_added`, `wishlist_price_drop(item, user, product, old_price, new_price)`, `question_asked`, `question_answered(question, answer)`, `product_viewed(product, user)` |
| marketplace | `vendor_applied`, `vendor_approved`, `vendor_status_changed(vendor, status)`, `vendor_order_created(vendor_order)`, `payout_created`, `payout_updated` |
| core | `event(name, payload)` — the generic JSON event behind webhooks; `audit_logged`, `webhook_triggered` |

Low/out-of-stock signals fire when the level **crosses** the threshold, not on every sale.

### Emitting your own

```python
from flexcommerce_core.events import emit
emit("loyalty.points_earned", signal=points_earned, sender=Points, payload={"user_id": str(user.pk)}, user=user)
```

`payload` (JSON) also goes to webhook subscribers of that event name.

## Hooks

```python
from flexcommerce_core import hooks

@hooks.register("order.created")
def reserve_courier_slot(order, lines, **kwargs):
    if not courier_available(order):
        raise CheckoutError("No delivery slots left today.")   # aborts checkout, nothing is saved
```

Or in settings: `FLEXCOMMERCE = {"HOOKS": {"order.created": ["myapp.hooks.reserve_courier_slot"]}}`.

| Hook | Signature | Built-in users |
|---|---|---|
| `price.modify` | `(product, price, user=None, quantity=1) -> price` | Flash sales |
| `order.created` | `(order, lines)` | Marketplace split, flash-sale unit claims |
| `order.confirmed`, `order.paid`, `order.delivered` | `(order, …)` | — |
| `order.cancelled` | `(order, actor)` | Restore coupon uses and flash-sale units |
| `refund.process` | `(refund, order) -> {"success", "reference", "error"}` or `None` | Payments (gateway / wallet refunds) |
| `catalog.vendor_id_for_user` | `(user) -> vendor UUID or None` | Marketplace (vendors manage their products) |

## Webhooks

Create a `WebhookEndpoint` in the admin (URL + event name, or `*` for everything). Deliveries are signed:

```
X-FlexCommerce-Event: order.paid
X-FlexCommerce-Delivery: 3f2c…
X-FlexCommerce-Signature: t=1727700000,v1=<hex HMAC-SHA256 of "t.<raw body>" with the endpoint secret>
```

```json
{"id": "3f2c…", "event": "order.paid", "created_at": "…",
 "data": {"order_number": "FC26093012345678", "status": "confirmed", "grand_total": "261500.00", "items": [...]}}
```

Verify in Python with `flexcommerce_core.webhooks.verify_signature(secret, raw_body, header)` (rejects
signatures older than 5 minutes). Failed deliveries retry with exponential backoff (1 min → 6 h, 8 attempts),
endpoints that keep failing are disabled, redirects are never followed, and URLs resolving to private or
metadata addresses are refused (SSRF protection).

Events: `order.created`, `order.confirmed`, `order.paid`, `order.processing`, `order.partially_shipped`,
`order.shipped`, `order.delivered`, `order.cancelled`, `order.refunded`, `order.partially_refunded`,
`payment.success`, `payment.failed`, `shipment.created`, `shipment.updated`, `refund.requested`,
`refund.processed`, `refund.failed`, `return.requested`, `return.updated`, `cart.abandoned`, `cart.recovered`,
`inventory.low_stock`, `inventory.out_of_stock`, `inventory.restocked`, `review.submitted`, `vendor.approved`,
`payout.created`.

## Notifications

`flexcommerce_notifications` is wired with the **outbox pattern**:

1. It subscribes to the signals above (only for apps that are installed).
2. For each event it works out the recipients — the customer (account or guest email/phone), staff, or the
   vendor — renders the message and writes one `NotificationLog` row per channel. A dedupe key guarantees the
   same event is never sent twice.
3. Each row is delivered through `ASYNC_EXECUTOR` (Celery in production). Failures are retried with backoff by
   the `notifications.retry` job; nothing is lost if a worker dies.

| Event | To | Channels |
|---|---|---|
| `order.created` | customer (+ staff "new order") | email, SMS, push, in-app |
| `order.paid`, `payment.failed` | customer | all |
| `order.shipped`, `shipment.out_for_delivery`, `order.delivered` | customer | all |
| `order.cancelled`, `refund.processed`, `return.updated` | customer | all |
| `cart.abandoned`, `wishlist.price_drop` | customer (marketing: opt-in) | all |
| `inventory.back_in_stock` | alert subscribers (incl. guests) | email, SMS |
| `question.answered` | asker | all |
| `wallet.credit` | customer | all |
| `vendor.approved`, `vendor.new_order`, `payout.updated` | vendor | all |
| `staff.order_created`, `inventory.low_stock`, `inventory.out_of_stock`, `review.submitted`, `return.requested`, `vendor.applied` | staff | email, in-app |

- **Templates:** every event has a built-in default. Override per event and channel in the admin
  (**Notification templates**, Django template syntax, e.g. `{{ order_number }}`,
  `{% if tracking_url %}…{% endif %}`); HTML emails use `flexcommerce/notifications/email_base.html`, which you
  can override in your templates directory.
- **Preferences:** customers choose per channel between order updates and marketing
  (`/notifications/preferences/`). Order updates always reach the in-app inbox.
- **Switches:** `NOTIFICATION_EVENTS = {"cart.abandoned": False, "order.shipped": ["sms", "push"]}`.
- **Providers:** email via Django's email backend; SMS via Termii, Africa's Talking, Twilio or Kudisms; push via
  FCM HTTP v1 or OneSignal; in-app built in. Write your own by subclassing
  `flexcommerce_notifications.backends.BaseNotificationBackend`.
- **Send your own:**

```python
from flexcommerce_notifications.dispatcher import Recipient, notify
notify("loyalty.points_earned", {"points": 50}, [Recipient(user=user)], dedupe=f"points:{txn.pk}")
```
