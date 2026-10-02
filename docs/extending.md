# Extending FlexCommerce

## Using your own product model

```python
class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)   # required: UUID pk
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=64, blank=True)
    price = models.DecimalField(max_digits=14, decimal_places=2)                  # required
    is_active = models.BooleanField(default=True)       # optional: not purchasable when False
    vat_exempt = models.BooleanField(default=False)     # optional: zero VAT
    weight = models.DecimalField(max_digits=8, decimal_places=3, default=0)   # optional: kg
    vendor_id = models.UUIDField(null=True, blank=True)  # optional: marketplace seller
    category_id = models.CharField(max_length=36, blank=True)   # optional: coupon restrictions

    def order_snapshot(self):          # optional: stored on the order line
        return {"image": self.image_url}

FLEXCOMMERCE = {"PRODUCT_MODELS": ["shop.Product"]}
```

Several models are allowed (`["shop.Book", "shop.Course"]`); clients then send `product_type`
(`"shop.book"`) or just the UUID. Without `flexcommerce_inventory`, a `stock` attribute is respected.

## Custom pricing

```python
class TieredPriceHandler:
    def get_price(self, product, user=None, quantity=1, cart=None):
        if user is not None and user.groups.filter(name="wholesale").exists():
            return product.wholesale_price
        return product.price

FLEXCOMMERCE = {"PRICE_HANDLER": "shop.pricing.TieredPriceHandler"}
```

Adjust any price without replacing the handler with the `price.modify` hook. Tax works the same way with a
`TAX_HANDLER` implementing `get_rate(product, user=None)`.

## A new payment gateway

```python
from flexcommerce_payments.gateways import BaseGateway, InitResult, VerifyResult, WebhookEvent

class MonnifyGateway(BaseGateway):
    name = "monnify"
    display_name = "Pay with Monnify"
    supports_refunds = True

    def is_available(self, order=None, user=None): ...
    def initiate(self, payment, callback_url="", customer=None) -> InitResult: ...
    def verify(self, payment) -> VerifyResult: ...          # must report amount + currency received
    def parse_webhook(self, body, headers) -> WebhookEvent: ...   # raise WebhookSignatureError if invalid
    def refund(self, payment, amount, reason="") -> dict: ...

from flexcommerce_payments.conf import DEFAULTS as PAYMENT_DEFAULTS

FLEXCOMMERCE = {
    "PAYMENT_GATEWAYS": {**PAYMENT_DEFAULTS["PAYMENT_GATEWAYS"], "monnify": "shop.payments.MonnifyGateway"},
}
```

FlexCommerce handles the rest: amount/currency verification, idempotent settlement, order confirmation,
webhook endpoint (`/payments/webhooks/monnify/`), refunds and wallet top-ups.

## Search engines

```python
def meilisearch_search(queryset, query):
    ids = meili.index("products").search(query, {"limit": 1000})["hits"]
    return queryset.filter(pk__in=[h["id"] for h in ids])

FLEXCOMMERCE = {"CATALOG_SEARCH_HANDLER": "shop.search.meilisearch_search"}
```

On PostgreSQL, `flexcommerce_catalog.search.postgres_search` gives ranked full-text search out of the box.

## SMS / push / email providers

```python
from flexcommerce_notifications.backends import BaseNotificationBackend

class BulkSMSNigeriaBackend(BaseNotificationBackend):
    channel = "sms"
    def send(self, recipient, subject, body, **kwargs):
        ...
        return self.ok(message_id) if ok else self.fail(error)

FLEXCOMMERCE = {"NOTIFICATION_SMS_BACKEND": "shop.sms.BulkSMSNigeriaBackend"}
```

## Order numbers

```python
FLEXCOMMERCE = {"ORDER_NUMBER_GENERATOR": "shop.orders.next_order_number"}   # must return a unique str
```

## Background executors

`ASYNC_EXECUTOR` accepts a dotted path to `f(path, args, kwargs)` for RQ, Dramatiq or Huey:

```python
def rq_executor(path, args, kwargs):
    django_rq.enqueue("flexcommerce_core.tasks._call", path, args, kwargs)
```

## Your own maintenance jobs

```python
from flexcommerce_core.jobs import register_job
register_job("loyalty.expire_points", "shop.loyalty.expire_points", interval_minutes=1440)
```

They run with `flexcommerce_run_jobs` alongside the built-in ones.

## Testing your project

```python
# conftest.py
pytest_plugins = ["flexcommerce_core.testing"]   # runs post-commit events inside tests, clears cache,
                                                  # provides fc(**settings) to override FLEXCOMMERCE
```
