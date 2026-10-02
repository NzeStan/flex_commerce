# API reference

All paths assume `path("api/", include("flexcommerce_core.urls"))`. Access: **Public** (anyone),
**Customer** (signed in), **Owner** (the customer who owns the object), **Staff** (`is_staff`),
**Vendor** (approved marketplace seller).

## Conventions

- JSON in and out. Money is a decimal **string** (`"150000.00"`).
- Lists are paginated: `{"count", "next", "previous", "results"}`; use `?page=` and `?page_size=` (max 100).
- Errors always look like
  `{"error": "insufficient_stock", "detail": "Insufficient stock.", "extra": {"available": 2, "requested": 3}}`.
  Validation errors use DRF's field format. Status codes: 400 invalid, 401/403 auth, 402 payment,
  404 not found, 409 conflict (state/locking/idempotency), 429 throttled.
- Anonymous carts: every cart response has `cart_token` (also the `X-Cart-Token` response header). Clients
  without cookies send it back as the `X-Cart-Token` request header.
- Products are identified by `product_id` (UUID) and optionally `product_type` (`"app_label.model"`, e.g.
  `"flexcommerce_catalog.productvariant"`); only configured models are accepted.

## Address book — `flexcommerce_core`

| Method | Path | Access | |
|---|---|---|---|
| GET, POST | `/addresses/` | Customer | Own addresses (first one becomes default) |
| GET, PATCH, DELETE | `/addresses/{id}/` | Owner | |
| POST | `/addresses/{id}/set-default/` | Owner | One default per type |

## Catalog — `flexcommerce_catalog`

| Method | Path | Access | |
|---|---|---|---|
| GET | `/catalog/categories/` | Public | `?parent=root\|<slug>`, `?tree=1` for the nested (cached) tree |
| POST, PATCH, DELETE | `/catalog/categories/{slug}/` | Staff | |
| GET | `/catalog/brands/` | Public | `?official=1` |
| GET | `/catalog/products/` | Public | Filters: `q`, `category` (incl. sub-categories), `brand=a,b`, `min_price`, `max_price`, `rating`, `in_stock=1`, `on_sale=1`, `featured=1`, `vendor`, `attr_<name>=<value>`; `ordering=newest\|price\|-price\|popularity\|rating\|name\|discount`; staff: `status`; vendors: `mine=1` |
| GET | `/catalog/products/facets/` | Public | Price range + brand counts for the current filters |
| GET | `/catalog/products/{slug or id}/` | Public | Variants (with `in_stock`), images, specs, breadcrumbs |
| GET | `/catalog/products/{slug}/related/` | Public | |
| POST, PATCH, DELETE | `/catalog/products/…` | Staff, Vendor (own) | Create with `variants=[…]` or `price` + `sku` |
| CRUD | `/catalog/variants/`, `/catalog/images/` | Staff, Vendor (own) | `?product=<id>`; images accept a file or a URL |

## Pricing — `flexcommerce_pricing`

| Method | Path | Access | |
|---|---|---|---|
| POST | `/pricing/calculate/` | Public | `{price, rate?, inclusive?}` → net / VAT / gross |
| POST | `/pricing/quote/` | Public | `{items: [{product_id, product_type?, quantity}]}` → authoritative prices (flash sales, tax) |
| GET / CRUD | `/pricing/tax-categories/` | Public / Staff | |

## Cart — `flexcommerce_cart`

| Method | Path | Access | |
|---|---|---|---|
| GET | `/cart/` | Public | Never creates a row for visitors without a cart |
| POST | `/cart/add/` | Public | `{product_id, product_type?, quantity}` |
| PATCH | `/cart/{item_id}/update/` | Public | `{quantity}` (0 removes) |
| DELETE | `/cart/{item_id}/remove/` | Public | |
| POST | `/cart/clear/` | Public | |
| POST / DELETE | `/cart/coupon/` / `/cart/coupon/remove/` | Public | Throttled (`coupon`) |
| POST | `/cart/contact/` | Public | `{email, phone}` — lets guests get cart-recovery emails |
| POST | `/cart/{item_id}/save/` | Customer | Save for later |
| GET | `/cart/saved/` | Customer | |
| POST | `/cart/{saved_id}/restore/` | Customer | |
| DELETE | `/cart/saved/{saved_id}/` | Customer | |

Cart responses include `subtotal`, `discount_amount`, `tax_total` (VAT included), `total`, `free_shipping`,
`coupon_error` (why a coupon was dropped), `promotion` (automatic promotion applied) and `notices`
(e.g. "X is no longer available and was removed").

## Checkout — `flexcommerce_checkout`

| Method | Path | Access | |
|---|---|---|---|
| POST | `/checkout/shipping-options/` | Public | `{state, country?}` or `{shipping_address_id}` → options for the current cart |
| POST | `/checkout/preview/` | Public | `{shipping_address, shipping_method_id}` → totals |
| POST | `/checkout/` | Public | Places the order (throttled `checkout`) |
| GET | `/checkout/payment-methods/` | Public | |

`POST /checkout/` fields: `payment_method` (gateway key or `card`), `shipping_address` or
`shipping_address_id`, `billing_address?`, `shipping_method_id?`, `pickup_station_id?`, `email` (guests),
`phone?`, `customer_note?`, `idempotency_key?`, `expected_total?`, `callback_url?`, `save_address?`.
Returns `201 {order, payment, requires_redirect, redirect_url, replayed, access_token?}` — `200` when replayed.

## Orders — `flexcommerce_orders`

| Method | Path | Access | |
|---|---|---|---|
| GET | `/orders/` | Customer (own) / Staff (all) | `?status=`; staff: `payment_status`, `search`, `from_date`, `to_date` |
| GET | `/orders/{id}/` | Owner, Staff | Items, shipments with tracking events, refunds, timeline |
| POST | `/orders/{id}/cancel/` | Owner, Staff | `{reason}` — releases stock and coupons, refunds paid money |
| POST | `/orders/{id}/return/` | Owner | `{items: [{order_item_id, quantity}], reason_code, reason, image_urls, refund_method}` |
| POST | `/orders/track/` | Public | `{order_number, email}` (throttled) |
| GET | `/orders/guest/{order_number}/?token=` | Public | Guest access link |
| POST | `/orders/{id}/transition/` | Staff | `{to_state, note}` |
| POST | `/orders/{id}/mark-paid/` | Staff | Bank transfer / cash on delivery |
| POST | `/orders/{id}/shipments/` | Staff | `{carrier, tracking_number, tracking_url, items?}` (partial shipments supported) |
| POST | `/orders/{id}/shipments/{shipment_id}/events/` | Staff | `{status, location, description}` |
| POST | `/orders/{id}/deliver/` | Staff | |
| POST | `/orders/{id}/refund/` | Staff | `{amount, reason, method: original\|wallet\|manual, process?}` |
| POST | `/orders/{id}/refunds/{refund_id}/process/` · `/reject/` | Staff | |
| GET | `/returns/` · `/returns/{id}/` | Customer (own) / Staff | |
| POST | `/returns/{id}/approve/` · `/reject/` · `/receive/` | Staff | `receive` restocks and creates the refund |

## Payments & wallet — `flexcommerce_payments`

| Method | Path | Access | |
|---|---|---|---|
| GET | `/payments/methods/?order_id=` | Public | |
| POST | `/payments/initiate/` | Owner, guest with token | `{provider, order_id \| order_number+token, callback_url?}` — (re)try paying an order |
| GET | `/payments/verify/?reference=` | Public | Call after the redirect; idempotent |
| POST | `/payments/webhooks/{provider}/` | Gateways | Signature-verified, CSRF-exempt |
| GET | `/payments/` | Customer (own) / Staff | |
| GET | `/wallet/` · `/wallet/transactions/` | Customer | |
| POST | `/wallet/topup/` | Customer | `{amount, provider}` |
| POST | `/wallet/adjust/` | Staff | `{user_id, amount (±), description}` |

## Inventory — `flexcommerce_inventory`

| Method | Path | Access | |
|---|---|---|---|
| GET, POST | `/inventory/` | Staff | `?low_stock=1`, `?search=`; create `{product_id, on_hand, reorder_point}` |
| POST | `/inventory/{id}/restock/` · `/adjust/` | Staff | |
| GET | `/inventory/{id}/movements/` · `/inventory/reservations/` | Staff | |
| POST | `/inventory/alerts/` | Public | Back-in-stock alert (`email` for guests) |
| GET, DELETE | `/inventory/alerts/…` | Customer | |

## Discounts — `flexcommerce_discounts`

| Method | Path | Access | |
|---|---|---|---|
| CRUD | `/coupons/` | Staff | `/coupons/{id}/usages/` |
| POST | `/coupons/validate/` | Public | `{code, cart_total?}` (throttled) |
| CRUD | `/flash-sales/` | Staff | `POST /flash-sales/{id}/items/ {product_id, sale_price?, quantity_limit?}` |
| GET | `/flash-sales/active/` | Public | |

## Shipping — `flexcommerce_shipping`

| Method | Path | Access | |
|---|---|---|---|
| GET | `/shipping/states/` | Public | 36 states + FCT with zones |
| POST | `/shipping/methods/calculate/` | Public | `{state, country?, cart_total, item_count, weight}` |
| GET | `/shipping/pickup-stations/` | Public | `?state=&city=` |
| CRUD | `/shipping/zones/`, `/shipping/methods/`, `/shipping/pickup-stations/` | Staff | |

## Engagement — `flexcommerce_engagement`

| Method | Path | Access | |
|---|---|---|---|
| CRUD | `/wishlists/` | Customer | Default list auto-created |
| POST | `/wishlists/toggle/` | Customer | Heart button |
| GET | `/wishlists/contains/?product_ids=a,b` | Customer | Which products are wishlisted |
| POST / DELETE | `/wishlists/{id}/add/` · `/remove/{item_id}/` · `/share/` | Owner | |
| POST | `/wishlists/{id}/items/{item_id}/move-to-cart/` | Owner | |
| GET | `/wishlists/shared/{token}/` | Public | |
| GET | `/reviews/?product_id=&rating=&verified=1&ordering=helpful\|newest\|highest\|lowest` | Public | |
| GET | `/reviews/summary/?product_id=` | Public | Average, count, 1–5 star distribution |
| POST | `/reviews/` | Customer | One per product; verified-purchase badge automatic |
| PATCH, DELETE | `/reviews/{id}/` | Owner, Staff | |
| POST | `/reviews/{id}/helpful/` | Customer | Toggle (one vote per user) |
| POST | `/reviews/{id}/approve/` · `/reject/` | Staff | |
| POST | `/reviews/{id}/reply/` | Staff, the product's vendor | |
| GET, POST | `/questions/` | Public / Customer | Product Q&A; `POST /questions/{id}/answers/` |
| GET, POST | `/recently-viewed/` (`track/`, `clear/`) | Public | Guests too; merged at login |
| GET, POST | `/recent-searches/` (`clear/`, `trending/`) | Public | |

## Marketplace — `flexcommerce_marketplace`

| Method | Path | Access | |
|---|---|---|---|
| GET | `/vendors/` · `/vendors/{slug}/` | Public | Approved shops |
| POST | `/vendors/apply/` | Customer | Throttled |
| GET, PATCH | `/vendors/me/` | Vendor (any status) | Profile, KYC, bank details |
| GET | `/vendors/me/summary/` · `/vendors/me/orders/` · `/vendors/me/payouts/` | Vendor | |
| POST | `/vendors/me/orders/{id}/ship/` | Vendor | Ships only the vendor's own items |
| PATCH / POST | `/vendors/{slug}/` · `approve/` `reject/` `suspend/` | Staff | |
| GET, POST | `/payouts/`, `/payouts/generate/`, `/payouts/{id}/mark-paid/` · `mark-failed/` | Staff | |

## Notifications — `flexcommerce_notifications`

| Method | Path | Access | |
|---|---|---|---|
| GET | `/notifications/inbox/` (`?unread=1`) · `unread-count/` | Customer | In-app inbox |
| POST | `/notifications/inbox/{id}/read/` · `read-all/` | Customer | |
| GET, PATCH | `/notifications/preferences/` | Customer | |
| POST, GET, DELETE | `/notifications/devices/` | Customer | Push tokens `{token, platform}` |
| GET | `/notifications/logs/my-notifications/` | Customer | |
| CRUD | `/notifications/templates/` (+ `preview/`) | Staff | |
| GET, POST | `/notifications/logs/` (+ `retry/`) | Staff | |

## Analytics — `flexcommerce_analytics` (Staff)

`/analytics/dashboard/`, `revenue/?period=daily|weekly|monthly`, `top-products/?by=revenue|quantity&limit=`,
`abandoned-carts/`, `conversion/`, `coupon-performance/`, `vendor-performance/`, `sales-by-state/`,
`payment-methods/`, `customers/`, `daily-summary/`, `export/?report=<name>` (CSV). All accept
`start_date` / `end_date` (YYYY-MM-DD; default last 30 days; max 3 years).
