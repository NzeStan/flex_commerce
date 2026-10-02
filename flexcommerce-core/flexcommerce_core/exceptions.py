"""
FlexCommerce unified exception hierarchy.

Every exception carries a machine-readable ``code``, a human ``message``,
optional ``extra`` data and the HTTP ``status_code`` the API layer should use.
Services raise these; views never need try/except for them because
``flexcommerce_core.api.exception_handler`` renders them consistently::

    {"error": "insufficient_stock", "detail": "Insufficient stock.", "extra": {...}}
"""


class FlexCommerceError(Exception):
    """Base exception for all FlexCommerce errors."""

    default_message = "A FlexCommerce error occurred."
    default_code = "flexcommerce_error"
    status_code = 400

    def __init__(self, message=None, code=None, extra=None):
        self.message = str(message) if message else self.default_message
        self.code = code or self.default_code
        self.extra = dict(extra or {})
        super().__init__(self.message)

    def __str__(self):
        return self.message

    def to_dict(self) -> dict:
        return {"error": self.code, "detail": self.message, "extra": self.extra}


class NotFoundError(FlexCommerceError):
    default_message = "Not found."
    default_code = "not_found"
    status_code = 404


class PermissionDeniedError(FlexCommerceError):
    default_message = "You do not have permission to perform this action."
    default_code = "permission_denied"
    status_code = 403


class ConfigurationError(FlexCommerceError):
    default_message = "FlexCommerce is not configured correctly."
    default_code = "configuration_error"
    status_code = 500


# ── Products ──────────────────────────────────────────────────────────────────


class ProductError(FlexCommerceError):
    default_message = "Product error."
    default_code = "product_error"


class InvalidProductTypeError(ProductError):
    default_message = "This product type cannot be purchased."
    default_code = "invalid_product_type"


class ProductNotFoundError(ProductError):
    default_message = "Product not found."
    default_code = "product_not_found"
    status_code = 404


class ProductUnavailableError(ProductError):
    default_message = "This product is not available for purchase."
    default_code = "product_unavailable"


# ── Cart ──────────────────────────────────────────────────────────────────────


class CartError(FlexCommerceError):
    default_message = "Cart error."
    default_code = "cart_error"


class CartNotFoundError(CartError):
    default_message = "Cart not found."
    default_code = "cart_not_found"
    status_code = 404


class CartLockedError(CartError):
    default_message = "Cart is locked during checkout."
    default_code = "cart_locked"
    status_code = 409


class CartExpiredError(CartError):
    default_message = "Cart has expired."
    default_code = "cart_expired"


class CartItemNotFoundError(CartError):
    default_message = "Cart item not found."
    default_code = "cart_item_not_found"
    status_code = 404


class CartLimitError(CartError):
    default_message = "Cart limit exceeded."
    default_code = "cart_limit_exceeded"


# ── Inventory ─────────────────────────────────────────────────────────────────


class InventoryError(FlexCommerceError):
    default_message = "Inventory error."
    default_code = "inventory_error"


class InsufficientStockError(InventoryError):
    default_message = "Insufficient stock."
    default_code = "insufficient_stock"

    def __init__(self, message=None, available=0, requested=0, **kwargs):
        self.available = available
        self.requested = requested
        extra = {
            "available": available,
            "requested": requested,
            **(kwargs.pop("extra", None) or {}),
        }
        super().__init__(message, extra=extra, **kwargs)


class StockReservationError(InventoryError):
    default_message = "Stock reservation failed."
    default_code = "stock_reservation_error"


# ── Pricing ───────────────────────────────────────────────────────────────────


class PricingError(FlexCommerceError):
    default_message = "Pricing error."
    default_code = "pricing_error"


# ── Discounts ─────────────────────────────────────────────────────────────────


class DiscountError(FlexCommerceError):
    default_message = "Discount error."
    default_code = "discount_error"


class CouponNotFoundError(DiscountError):
    default_message = "Coupon not found."
    default_code = "coupon_not_found"
    status_code = 404


class CouponExpiredError(DiscountError):
    default_message = "Coupon has expired or is not active."
    default_code = "coupon_expired"


class CouponUsageLimitError(DiscountError):
    default_message = "Coupon usage limit reached."
    default_code = "coupon_usage_limit"


class CouponMinimumValueError(DiscountError):
    default_message = "Cart total does not meet the minimum required for this coupon."
    default_code = "coupon_minimum_value"


class CouponAlreadyAppliedError(DiscountError):
    default_message = "Coupon already applied to this cart."
    default_code = "coupon_already_applied"


class CouponNotApplicableError(DiscountError):
    default_message = "Coupon does not apply to any item in your cart."
    default_code = "coupon_not_applicable"


# ── Shipping ──────────────────────────────────────────────────────────────────


class ShippingError(FlexCommerceError):
    default_message = "Shipping error."
    default_code = "shipping_error"


class ShippingMethodNotAvailableError(ShippingError):
    default_message = "Shipping method not available for this address."
    default_code = "shipping_method_unavailable"


# ── Orders ────────────────────────────────────────────────────────────────────


class OrderError(FlexCommerceError):
    default_message = "Order error."
    default_code = "order_error"


class InvalidOrderTransitionError(OrderError):
    default_message = "Invalid order state transition."
    default_code = "invalid_order_transition"
    status_code = 409


class OrderCancellationError(OrderError):
    default_message = "Order cannot be cancelled in its current state."
    default_code = "order_cancellation_error"
    status_code = 409


class RefundError(OrderError):
    default_message = "Refund error."
    default_code = "refund_error"


class ReturnError(OrderError):
    default_message = "Return request error."
    default_code = "return_error"


# ── Checkout ──────────────────────────────────────────────────────────────────


class CheckoutError(FlexCommerceError):
    default_message = "Checkout error."
    default_code = "checkout_error"


class EmptyCartCheckoutError(CheckoutError):
    default_message = "Cannot checkout with an empty cart."
    default_code = "empty_cart_checkout"


class PaymentError(CheckoutError):
    default_message = "Payment processing error."
    default_code = "payment_error"
    status_code = 402


class DuplicateCheckoutError(CheckoutError):
    default_message = "Duplicate checkout detected."
    default_code = "duplicate_checkout"
    status_code = 409


class GuestCheckoutDisabledError(CheckoutError):
    default_message = "Please sign in to complete checkout."
    default_code = "guest_checkout_disabled"
    status_code = 401


# ── Payments ──────────────────────────────────────────────────────────────────


class PaymentVerificationError(PaymentError):
    default_message = "Payment could not be verified."
    default_code = "payment_verification_failed"


class WebhookSignatureError(FlexCommerceError):
    default_message = "Invalid webhook signature."
    default_code = "invalid_signature"
    status_code = 401


class WalletError(FlexCommerceError):
    default_message = "Wallet error."
    default_code = "wallet_error"


class InsufficientFundsError(WalletError):
    default_message = "Insufficient wallet balance."
    default_code = "insufficient_funds"
    status_code = 402


# ── Marketplace ───────────────────────────────────────────────────────────────


class VendorError(FlexCommerceError):
    default_message = "Vendor error."
    default_code = "vendor_error"


class VendorNotApprovedError(VendorError):
    default_message = "Your vendor account is not approved yet."
    default_code = "vendor_not_approved"
    status_code = 403
