"""
FlexCommerce checkout orchestrator.

    result = CheckoutService(cart, request).execute(payment_method="paystack", shipping_address={...}, ...)

Inside ONE database transaction:
  lock cart → re-price (fresh prices, coupon re-validated) → shipping quote for the
  address → totals → create order → reserve stock → redeem coupon → close cart
  (→ confirm immediately for pay-on-delivery, settle wallet payments).
After commit:
  start the online payment (network call) and return the redirect URL.

Retrying with the same ``idempotency_key`` returns the original order.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from flexcommerce_core.conf import fc_setting
from flexcommerce_core.events import emit
from flexcommerce_core.exceptions import (
    CartLockedError,
    CheckoutError,
    DuplicateCheckoutError,
    EmptyCartCheckoutError,
    FlexCommerceError,
    GuestCheckoutDisabledError,
    PaymentError,
    ShippingMethodNotAvailableError,
)
from flexcommerce_core.utils.helpers import registry
from flexcommerce_core.utils.products import is_installed
from flexcommerce_core.utils.vat import ZERO, round_price, to_decimal

from .conf import checkout_setting

logger = logging.getLogger("flexcommerce.checkout")

LEGACY_METHODS = {"card", "bank_transfer", "wallet", "pay_on_delivery"}
CATEGORY = {
    "pay_on_delivery": "pay_on_delivery",
    "bank_transfer": "bank_transfer",
    "wallet": "wallet",
}


class PriceChangedError(CheckoutError):
    default_message = "Prices in your cart changed. Please review your order."
    default_code = "price_changed"
    status_code = 409


# ── Simple payment handler API (still supported when flexcommerce_payments is absent) ──


class PaymentAbstraction:
    """Register via ``FLEXCOMMERCE = {"PAYMENT_HANDLER": "myapp.payments.MyHandler"}``."""

    def initiate(self, order, amount: Decimal, method: str, **kwargs) -> dict:
        raise NotImplementedError

    def verify(self, reference: str) -> dict:
        raise NotImplementedError


class PayOnDeliveryHandler(PaymentAbstraction):
    def initiate(self, order, amount, method, **kwargs):
        limit = fc_setting("PAY_ON_DELIVERY_LIMIT")
        if limit is not None and to_decimal(amount) > to_decimal(limit):
            raise PaymentError(f"Pay on Delivery is not available for orders above {to_decimal(limit):,.2f}.")
        return {"reference": f"POD-{order.order_number}", "redirect_url": None, "status": "pending"}

    def verify(self, reference):
        return {"status": "pending", "reference": reference}


@dataclass
class CheckoutResult:
    order: object
    payment_result: dict = field(default_factory=dict)
    replayed: bool = False

    @property
    def requires_redirect(self):
        return bool(self.payment_result.get("redirect_url"))

    @property
    def redirect_url(self):
        return self.payment_result.get("redirect_url")

    def to_dict(self, context=None):
        from flexcommerce_orders.serializers import OrderSerializer

        data = {
            "order": OrderSerializer(self.order, context=context or {}).data,
            "payment": self.payment_result,
            "requires_redirect": self.requires_redirect,
            "redirect_url": self.redirect_url,
            "replayed": self.replayed,
        }
        if self.order.user_id is None:
            data["access_token"] = self.order.access_token
        return data


def replay_for_request(request, idempotency_key):
    """The order a previous request created with ``idempotency_key`` (owner-checked), or ``None``."""
    from flexcommerce_cart.models import Cart
    from flexcommerce_cart.services import CartSessionManager
    from flexcommerce_orders.models import Order

    order = Order.objects.filter(idempotency_key=idempotency_key).first()
    if order is None:
        return None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and order.user_id == user.pk:
        owned = True
    else:
        token = CartSessionManager._header_token(request) or CartSessionManager._session_token(request)
        owned = bool(token) and Cart.objects.filter(pk=order.cart_id, token=token).exists()
    if not owned:
        raise DuplicateCheckoutError()
    cart = Cart.objects.filter(pk=order.cart_id).first() or Cart(pk=order.cart_id, user=order.user)
    return CheckoutService(cart, request)._replay(idempotency_key)


def available_payment_methods(order=None, user=None):
    if is_installed("flexcommerce_payments"):
        from flexcommerce_payments.gateways import available_gateways

        return [g.describe(order) for g in available_gateways(order=order, user=user)]
    return [
        {"key": "card", "label": "Card Payment", "type": "redirect"},
        {"key": "bank_transfer", "label": "Bank Transfer", "type": "offline"},
        {"key": "wallet", "label": "Wallet", "type": "instant"},
        {
            "key": "pay_on_delivery",
            "label": "Pay on Delivery",
            "type": "offline",
            "limit": fc_setting("PAY_ON_DELIVERY_LIMIT"),
        },
    ]


class CheckoutService:
    def __init__(self, cart, request=None):
        self.cart = cart
        self.request = request

    # ── helpers ──────────────────────────────────────────────────────────────
    def _resolve_method(self, payment_method):
        """Return ``(gateway_key, order_category)``."""
        if is_installed("flexcommerce_payments"):
            from flexcommerce_payments.gateways import gateway_paths

            key = checkout_setting("DEFAULT_CARD_GATEWAY") if payment_method == "card" else payment_method
            if key not in gateway_paths():
                raise PaymentError("Unknown payment method.", code="unknown_payment_method")
            return key, CATEGORY.get(key, "card")
        if payment_method not in LEGACY_METHODS:
            raise PaymentError("Unknown payment method.", code="unknown_payment_method")
        return payment_method, CATEGORY.get(payment_method, "card")

    def _address(self, shipping_address=None, shipping_address_id=None):
        from flexcommerce_core.models import Address
        from flexcommerce_core.serializers import AddressInputSerializer

        if shipping_address_id:
            user = self.cart.user
            address = Address.objects.filter(pk=shipping_address_id, user=user).first() if user else None
            if address is None:
                raise CheckoutError("Address not found.", code="address_not_found")
            return address.to_snapshot()
        ser = AddressInputSerializer(data=shipping_address or {})
        if not ser.is_valid():
            raise CheckoutError("Invalid address.", code="invalid_address", extra=ser.errors)
        return dict(ser.validated_data)

    def _shipping(self, method_id, address, lines, free_shipping, pickup_station_id, total):
        if not is_installed("flexcommerce_shipping"):
            return None
        if not method_id:
            if checkout_setting("CHECKOUT_REQUIRE_SHIPPING"):
                raise ShippingMethodNotAvailableError("Choose a delivery method.", code="shipping_method_required")
            return None
        from flexcommerce_shipping.services import quote_method

        weight = sum((line.weight * line.quantity for line in lines), ZERO)
        return quote_method(
            method_id,
            state=address.get("state", ""),
            country=address.get("country", ""),
            cart_total=total,
            item_count=sum(line.quantity for line in lines),
            weight=weight,
            free_shipping=free_shipping,
            pickup_station_id=pickup_station_id,
        )

    def _payment_due(self, category):
        from flexcommerce_orders.conf import orders_setting

        now = timezone.now()
        if category == "bank_transfer":
            return now + timedelta(hours=orders_setting("BANK_TRANSFER_TIMEOUT_HOURS"))
        if category in ("pay_on_delivery", "wallet"):
            return None
        return now + timedelta(minutes=orders_setting("UNPAID_ORDER_TIMEOUT_MINUTES"))

    def _replay(self, idempotency_key):
        from flexcommerce_orders.models import Order

        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing is None:
            return None
        same_owner = (existing.user_id and existing.user_id == self.cart.user_id) or existing.cart_id == self.cart.pk
        if not same_owner:
            raise DuplicateCheckoutError()
        payment = {}
        if is_installed("flexcommerce_payments"):
            latest = existing.payments.order_by("-created_at").first()
            if latest is not None:
                payment = _payment_dict(latest)
        return CheckoutResult(order=existing, payment_result=payment, replayed=True)

    # ── preview (no side effects) ────────────────────────────────────────────
    def preview(
        self,
        shipping_address=None,
        shipping_address_id=None,
        shipping_method_id=None,
        pickup_station_id=None,
    ):
        from flexcommerce_cart.services import CartPricingService

        with transaction.atomic():
            lines, result = CartPricingService(self.cart).recalculate()
        self.cart.refresh_from_db()
        quote = None
        if shipping_method_id or shipping_address or shipping_address_id:
            address = self._address(shipping_address, shipping_address_id)
            quote = (
                self._shipping(
                    shipping_method_id,
                    address,
                    lines,
                    self.cart.free_shipping,
                    pickup_station_id,
                    self.cart.total_amount,
                )
                if shipping_method_id
                else None
            )
        return self._totals(quote)

    def _totals(self, quote):
        cart = self.cart
        shipping = quote.gross if quote else ZERO
        shipping_vat = quote.vat if quote else ZERO
        return {
            "subtotal": cart.subtotal_amount,
            "discount": cart.discount_amount,
            "shipping": shipping,
            "shipping_vat": shipping_vat,
            "tax_total": round_price(cart.tax_amount + shipping_vat),
            "grand_total": round_price(cart.total_amount + shipping),
            "currency": cart.currency,
            "coupon_code": cart.coupon_code,
            "coupon_error": cart.coupon_error,
            "free_shipping": cart.free_shipping,
            "shipping_option": quote.to_dict() if quote else None,
        }

    # ── checkout ─────────────────────────────────────────────────────────────
    def execute(
        self,
        payment_method,
        shipping_address=None,
        billing_address=None,
        shipping_method_id=None,
        customer_note="",
        idempotency_key=None,
        email="",
        phone="",
        pickup_station_id=None,
        shipping_address_id=None,
        callback_url="",
        expected_total=None,
        save_address=None,
    ) -> CheckoutResult:
        user = self.cart.user
        if user is None and not checkout_setting("GUEST_CHECKOUT"):
            raise GuestCheckoutDisabledError()
        email = (email or getattr(user, "email", "") or self.cart.email or "").strip().lower()
        if user is None and not email:
            raise CheckoutError("Email is required for guest checkout.", code="email_required")
        if idempotency_key:
            replay = self._replay(idempotency_key)
            if replay is not None:
                return replay
        gateway_key, category = self._resolve_method(payment_method)
        address = self._address(shipping_address, shipping_address_id)
        billing = self._address(billing_address) if billing_address else address

        try:
            order = self._place(
                gateway_key,
                category,
                address,
                billing,
                shipping_method_id,
                pickup_station_id,
                customer_note,
                idempotency_key,
                email,
                phone or address.get("phone", ""),
                expected_total,
                save_address,
            )
        except IntegrityError:
            if idempotency_key:
                replay = self._replay(idempotency_key)  # a parallel request won the race
                if replay is not None:
                    return replay
            raise
        payment_result = self._start_payment(order, gateway_key, category, callback_url)
        order.refresh_from_db()
        return CheckoutResult(order=order, payment_result=payment_result)

    @transaction.atomic
    def _place(
        self,
        gateway_key,
        category,
        address,
        billing,
        shipping_method_id,
        pickup_station_id,
        customer_note,
        idempotency_key,
        email,
        phone,
        expected_total,
        save_address,
    ):
        from flexcommerce_cart import signals as cart_signals
        from flexcommerce_cart.models import Cart
        from flexcommerce_cart.services import CartPricingService
        from flexcommerce_orders.services import OrderService, place_order

        cart = Cart.objects.select_for_update().get(pk=self.cart.pk)
        if cart.status == Cart.STATUS_LOCKED:
            raise CartLockedError()
        if cart.status != Cart.STATUS_ACTIVE:
            raise CheckoutError("This cart has already been checked out.", code="cart_inactive")
        self.cart = cart
        lines, discount_result = CartPricingService(cart).recalculate()
        if not lines:
            raise EmptyCartCheckoutError()
        allocations = discount_result.allocations if discount_result else {}
        for line in lines:
            line.discount = allocations.get(line.key, ZERO)

        quote = self._shipping(
            shipping_method_id,
            address,
            lines,
            cart.free_shipping,
            pickup_station_id,
            cart.total_amount,
        )
        if category == "pay_on_delivery" and quote is not None and not quote.method.pay_on_delivery:
            raise PaymentError(
                "Pay on delivery is not available for this delivery method.",
                code="pod_not_available",
            )
        totals = self._totals(quote)
        if expected_total is not None and round_price(to_decimal(expected_total)) != totals["grand_total"]:
            raise PriceChangedError(
                extra={
                    "expected": str(expected_total),
                    "actual": str(totals["grand_total"]),
                    "notices": (cart.extra_data or {}).get("notices", []),
                }
            )
        if category == "pay_on_delivery":
            limit = fc_setting("PAY_ON_DELIVERY_LIMIT")
            if limit not in (None, "") and totals["grand_total"] > to_decimal(limit):
                raise PaymentError(
                    f"Pay on delivery is only available for orders up to {to_decimal(limit):,.2f}.",
                    code="pod_limit_exceeded",
                    extra={"limit": str(limit)},
                )

        payment_due = self._payment_due(category)
        promo_code = cart.coupon_code or ((cart.extra_data or {}).get("promotion") or {}).get("code", "")
        order = place_order(
            lines=lines,
            subtotal=totals["subtotal"],
            discount=totals["discount"],
            tax_total=totals["tax_total"],
            shipping_cost=totals["shipping"],
            shipping_vat=totals["shipping_vat"],
            grand_total=totals["grand_total"],
            currency=cart.currency,
            shipping_address=address,
            billing_address=billing,
            payment_method=category,
            user=cart.user,
            email=email,
            phone=phone,
            session_key=cart.session_key,
            cart_id=cart.pk,
            idempotency_key=idempotency_key or "",
            coupon_code=promo_code,
            customer_note=customer_note,
            shipping_method=quote.method if quote else None,
            pickup_station=quote.pickup_station.to_snapshot() if quote and quote.pickup_station else None,
            estimated_delivery=quote.method.estimated_delivery() if quote else None,
            payment_due_at=payment_due,
        )
        order.payment_provider = gateway_key
        order.save(update_fields=["payment_provider", "updated_at"])

        if is_installed("flexcommerce_inventory"):
            from flexcommerce_inventory.services import reserve_lines

            ttl = int((payment_due - timezone.now()).total_seconds() // 60) + 1 if payment_due else 0
            reserve_lines(
                [(line.product, line.quantity) for line in lines],
                order.order_number,
                ttl_minutes=ttl,
            )

        if promo_code and is_installed("flexcommerce_discounts"):
            from flexcommerce_discounts.services import redeem

            redeem(
                promo_code,
                order_ref=order.order_number,
                amount=totals["discount"],
                user=cart.user,
                email=email,
                session_key=cart.session_key,
            )

        if save_address is not False and cart.user_id and checkout_setting("CHECKOUT_SAVE_ADDRESSES"):
            self._remember_address(cart.user, address)

        cart.status = Cart.STATUS_ORDERED
        cart.save(update_fields=["status", "updated_at"])
        if cart.abandoned_notified_at:
            emit(
                "cart.recovered",
                signal=cart_signals.cart_recovered,
                sender=Cart,
                payload={"cart_id": str(cart.pk), "order_number": order.order_number},
                cart=cart,
                order=order,
            )

        if category == "pay_on_delivery":
            OrderService(order).confirm(actor="checkout")
        elif category == "wallet":
            self._pay_with_wallet(order, gateway_key)
        return order

    @staticmethod
    def _remember_address(user, address):
        from flexcommerce_core.models import Address

        fields = {k: address.get(k, "") for k in Address.SNAPSHOT_FIELDS if k in address}
        if not Address.objects.filter(user=user, line1=fields.get("line1", ""), city=fields.get("city", "")).exists():
            Address.objects.create(user=user, is_default=not Address.objects.filter(user=user).exists(), **fields)

    def _pay_with_wallet(self, order, gateway_key):
        if not is_installed("flexcommerce_payments"):
            raise PaymentError("Wallet payments are not enabled.", code="payment_method_unavailable")
        from flexcommerce_payments.services import PaymentService

        payment = PaymentService.start(order, gateway_key, user=order.user)
        if payment.status != "success":
            raise PaymentError(payment.failure_reason or "Wallet payment failed.", code="wallet_payment_failed")

    def _start_payment(self, order, gateway_key, category, callback_url):
        """After commit: create the payment / redirect. Failures leave the order pending."""
        if category in ("pay_on_delivery",) and not is_installed("flexcommerce_payments"):
            return PayOnDeliveryHandler().initiate(order, order.grand_total, category)
        if is_installed("flexcommerce_payments"):
            from flexcommerce_payments.models import Payment
            from flexcommerce_payments.services import PaymentService

            if category == "wallet":
                payment = order.payments.filter(status=Payment.STATUS_SUCCESS).order_by("-created_at").first()
                return _payment_dict(payment) if payment else {}
            try:
                payment = PaymentService.start(order, gateway_key, callback_url=callback_url, user=order.user)
            except FlexCommerceError as exc:
                logger.warning("Payment could not be started for %s: %s", order.order_number, exc)
                return {
                    "status": "failed",
                    "error": exc.code,
                    "detail": exc.message,
                    "retry_url": "payments/initiate/",
                }
            return _payment_dict(payment)
        try:
            handler = registry.get("PAYMENT_HANDLER")
        except KeyError:
            return {"status": "pending", "reference": ""}
        result = handler.initiate(order, order.grand_total, category)
        if result.get("status") == "success":
            from flexcommerce_orders.services import OrderService

            OrderService(order).mark_paid(reference=result.get("reference", ""), provider="custom", actor="checkout")
        return result


def _payment_dict(payment):
    instructions = (payment.gateway_response or {}).get("instructions") or None
    return {
        "reference": payment.reference,
        "provider": payment.provider,
        "status": payment.status,
        "amount": str(payment.amount),
        "redirect_url": payment.authorization_url or None,
        "instructions": instructions,
    }
