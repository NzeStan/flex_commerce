# Settings reference

All options live in one dict in your Django settings:

```python
FLEXCOMMERCE = {"VAT_INCLUSIVE": True, "STORE_NAME": "Naija Mart"}
```

Defaults below are the values used when you don't set a key. Options only apply when their app is installed.

## Core (`flexcommerce_core`)

| Key | Default | Description |
|---|---|---|
| `PRODUCT_MODELS` | `None` | Purchasable models (`"app.Model"`). `None` = `flexcommerce_catalog.ProductVariant` if the catalog is installed. |
| `CURRENCY` / `CURRENCY_SYMBOL` | `"NGN"` / `"₦"` | Store currency. |
| `DECIMAL_PLACES` | `2` | Money rounding (half-up). |
| `VAT_RATE` | `0.075` | Default VAT rate (7.5%, Nigeria). |
| `VAT_INCLUSIVE` | `False` | `True` if catalogue prices already include VAT. |
| `VAT_ON_SHIPPING` | `False` | Charge VAT on shipping methods with `apply_vat`. |
| `MARKETPLACE_MODE` | `False` | Enables vendor reports in analytics. |
| `ASYNC_EXECUTOR` | `"sync"` | `"sync"`, `"threading"`, `"celery"` or a dotted path to `f(path, args, kwargs)`. |
| `AUDIT_ENABLED` | `True` | Write `AuditLog` rows for state changes. |
| `SOFT_DELETE` | `True` | `SoftDeleteModel.delete()` marks rows deleted instead of removing them. |
| `HOOKS` | `{}` | Extra hooks: `{"order.created": ["myapp.hooks.fn"]}`. |
| `PAGE_SIZE` / `MAX_PAGE_SIZE` | `20` / `100` | API pagination (`?page=`, `?page_size=`). |
| `THROTTLING_ENABLED` | `True` | Master switch for FlexCommerce throttles. |
| `THROTTLE_RATES` | `{}` | Overrides of `checkout 30/hour`, `coupon 30/minute`, `review 30/hour`, `payment 60/hour`, `guest_order_lookup 20/hour`, `vendor_signup 5/hour`, `stock_alert 30/hour`. `None` disables one. |
| `WEBHOOK_TIMEOUT` | `10` | Seconds per outgoing webhook call. |
| `WEBHOOK_MAX_ATTEMPTS` | `8` | Retries with exponential backoff (1 min … 6 h). |
| `WEBHOOK_DISABLE_AFTER_FAILURES` | `50` | Consecutive failures before an endpoint is disabled. |
| `WEBHOOK_REQUIRE_HTTPS` | `True` | Refuse plain-HTTP webhook URLs. |
| `WEBHOOK_ALLOW_PRIVATE_URLS` | `False` | Allow private/loopback targets (development only — SSRF protection). |
| `LOW_STOCK_THRESHOLD` | `5` | Low-stock alert level for items without their own threshold. |
| `ALLOW_OVERSELL` | `False` | Allow selling beyond stock everywhere. |
| `PRICE_HANDLER` / `TAX_HANDLER` | built-in | Dotted paths to custom handlers (see [extending](extending.md)). |

## Catalog

| Key | Default | Description |
|---|---|---|
| `CATALOG_SEARCH_HANDLER` | `"flexcommerce_catalog.search.basic_search"` | `postgres_search` for full-text ranking, or your own (Elasticsearch, Meilisearch…). |
| `CATALOG_CATEGORY_CACHE_SECONDS` | `300` | Cache for `?tree=1`. Invalidated on every category change. |
| `CATALOG_MAX_CATEGORY_DEPTH` | `8` | Maximum category nesting. |
| `CATALOG_RELATED_LIMIT` | `12` | Products returned by `/related/`. |

## Inventory

| Key | Default | Description |
|---|---|---|
| `INVENTORY_TRACK_BY_DEFAULT` | `False` | `True`: products without an inventory record are out of stock. |
| `STOCK_RESERVATION_MINUTES` | `60` | Default hold for reservations (checkout aligns it with the payment deadline). |

## Discounts

| Key | Default | Description |
|---|---|---|
| `DISCOUNT_CACHE_SECONDS` | `30` | Cache for running flash sales and automatic promotions. |
| `AUTO_APPLY_PROMOTIONS` | `True` | Apply the best `auto_apply` coupon when the cart has no code. |

## Shipping

| Key | Default | Description |
|---|---|---|
| `SHIPPING_HOME_COUNTRY` | `"Nigeria"` | Addresses in this country are matched by state; others by the zone's `countries`. |

## Cart

| Key | Default | Description |
|---|---|---|
| `CART_TOKEN_HEADER` | `"X-Cart-Token"` | Header carrying the anonymous cart token (mobile / SPA). |
| `CART_SESSION_KEY` | `"flexcommerce_cart"` | Session key holding the token for browser clients. |
| `CART_EXPIRY_DAYS` | `30` | Anonymous carts expire after this. |
| `CART_PURGE_DAYS` | `90` | Expired/merged anonymous carts are deleted after this. |
| `CART_ABANDONMENT_HOURS` | `1` | Idle time before `cart.abandoned` fires (once per cart). |
| `CART_MAX_QUANTITY_PER_ITEM` | `100` | Per-line quantity cap. |
| `CART_MAX_ITEMS` | `100` | Distinct lines per cart. |
| `CART_MERGE_ON_LOGIN` | `True` | Merge the anonymous cart into the user's cart at login. |

## Orders

| Key | Default | Description |
|---|---|---|
| `ORDER_NUMBER_PREFIX` | `"FC"` | Order numbers look like `FC26093012345678`. |
| `ORDER_NUMBER_GENERATOR` | `None` | Dotted path to your own generator. |
| `UNPAID_ORDER_TIMEOUT_MINUTES` | `60` | Online-payment deadline; then the order is cancelled and stock released. |
| `BANK_TRANSFER_TIMEOUT_HOURS` | `48` | Deadline for bank-transfer orders. |
| `CUSTOMER_CANCELLABLE_STATUSES` | `["pending", "confirmed"]` | Statuses in which customers may cancel. |
| `RETURN_WINDOW_DAYS` | `7` | Days after delivery to request a return (`0` disables returns). |
| `AUTO_PROCESS_CANCELLATION_REFUNDS` | `False` | Pay refunds for cancelled paid orders immediately instead of staff approval. |

## Payments

| Key | Default | Description |
|---|---|---|
| `PAYMENT_GATEWAYS` | paystack, flutterwave, bank_transfer, pay_on_delivery, wallet | `{"key": "dotted.Gateway"}` — add or remove gateways. |
| `PAYSTACK_SECRET_KEY` / `PAYSTACK_PUBLIC_KEY` | `""` | Paystack is available once the secret is set. |
| `FLUTTERWAVE_SECRET_KEY` / `FLUTTERWAVE_PUBLIC_KEY` | `""` | Flutterwave v3. |
| `FLUTTERWAVE_WEBHOOK_HASH` | `""` | The "secret hash" from the Flutterwave dashboard (required for webhooks). |
| `BANK_TRANSFER_ACCOUNTS` | `[]` | Accounts shown to customers; bank transfer is offered only when set. |
| `PAY_ON_DELIVERY_ENABLED` | `True` | |
| `PAY_ON_DELIVERY_LIMIT` | `None` | Maximum order total for pay on delivery. |
| `PAYMENT_CALLBACK_URL` | `""` | Where gateways return the customer. |
| `PAYMENT_ALLOWED_CALLBACK_HOSTS` | `[]` | Hosts clients may pass as `callback_url` (open-redirect protection). |
| `PAYMENT_HTTP_TIMEOUT` | `30` | Gateway API timeout (seconds). |
| `WALLET_ENABLED` / `WALLET_MAX_BALANCE` / `WALLET_MIN_TOPUP` | `True` / `None` / `100` | Customer wallet. |

## Checkout

| Key | Default | Description |
|---|---|---|
| `GUEST_CHECKOUT` | `True` | Allow checkout without an account (email required). |
| `CHECKOUT_REQUIRE_SHIPPING` | `True` | Require a delivery method when the shipping app is installed. |
| `DEFAULT_CARD_GATEWAY` | `"paystack"` | Gateway used for the generic `"card"` method. |
| `CHECKOUT_SAVE_ADDRESSES` | `True` | Save new addresses to the signed-in customer's address book. |
| `PAYMENT_HANDLER` | — | Simple payment handler, used when `flexcommerce_payments` is not installed. |

## Engagement

| Key | Default | Description |
|---|---|---|
| `ENGAGEMENT_MODELS` | `None` | Reviewable / wishlistable models. `None` = catalog `Product` + `PRODUCT_MODELS`. |
| `REVIEWS_REQUIRE_APPROVAL` | `True` | Reviews are published after moderation (edits re-enter moderation). |
| `REVIEWS_VERIFIED_ONLY` | `False` | Only buyers may review. |
| `QUESTIONS_REQUIRE_APPROVAL` | `True` | |
| `RECENTLY_VIEWED_LIMIT` / `RECENT_SEARCHES_LIMIT` | `20` / `10` | History length per customer. |
| `WISHLIST_PRICE_DROP_PERCENT` | `5` | Minimum drop that triggers a price alert. |

## Marketplace

| Key | Default | Description |
|---|---|---|
| `MARKETPLACE_COMMISSION_RATE` | `0.10` | Default commission (per-vendor override in admin). |
| `VENDOR_AUTO_APPROVE` | `False` | |
| `VENDOR_PAYOUT_HOLD_DAYS` | `None` | Days after delivery before earnings are payable (`None` = `RETURN_WINDOW_DAYS`). |
| `VENDOR_MINIMUM_PAYOUT` | `1000` | |

## Notifications

| Key | Default | Description |
|---|---|---|
| `NOTIFICATION_CHANNELS` | `["email", "sms", "push", "in_app"]` | Enabled channels. |
| `NOTIFICATION_EMAIL_BACKEND` | `DjangoEmailBackend` | Uses Django's `EMAIL_BACKEND` (SMTP, SES, Anymail…). |
| `NOTIFICATION_SMS_BACKEND` | `NullBackend` | `backends.sms.TermiiSMSBackend`, `AfricasTalkingSMSBackend`, `TwilioSMSBackend`, `KudismsSMSBackend`. |
| `NOTIFICATION_PUSH_BACKEND` | `NullBackend` | `backends.push.FCMPushBackend` (HTTP v1) or `OneSignalPushBackend`. |
| `NOTIFICATION_EVENTS` | `{}` | `{"cart.abandoned": False, "order.shipped": ["sms", "push"]}`. |
| `NOTIFICATION_MAX_ATTEMPTS` | `5` | Retries with backoff. |
| `STAFF_NOTIFICATION_EMAILS` | `[]` | Staff alert recipients (default: active staff users). |
| `STAFF_NOTIFICATION_EVENTS` | new order, low/out of stock, review, return, vendor application | |
| `FRONTEND_URL` / `ORDER_URL_TEMPLATE` | `""` / `"{frontend}/orders/{order_number}"` | Links in messages. |
| `STORE_NAME` | `"Our store"` | |

Provider credentials: `TERMII_API_KEY`, `TERMII_SENDER_ID`, `TERMII_BASE_URL`, `TERMII_CHANNEL`; `AT_USERNAME`,
`AT_API_KEY`, `AT_SENDER_ID`; `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`; `KUDISMS_TOKEN`,
`KUDISMS_SENDER_ID`, `KUDISMS_API_URL`; `FCM_PROJECT_ID`, `FCM_SERVICE_ACCOUNT_FILE` or `FCM_SERVICE_ACCOUNT_INFO`;
`ONESIGNAL_APP_ID`, `ONESIGNAL_API_KEY`.
