"""
Payment and wallet services.

Flow for redirect gateways (Paystack / Flutterwave)::

    payment = PaymentService.start(order, "paystack", callback_url=...)
    → redirect the customer to payment.authorization_url
    → provider calls our webhook  AND/OR  the client calls /payments/verify/?reference=
    → PaymentService.verify(reference) re-checks with the provider's API and, if the
      right amount in the right currency was received, marks the order paid.

``verify`` is idempotent and safe to call concurrently (row lock on the payment).
"""

import logging
import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from flexcommerce_core.events import emit
from flexcommerce_core.exceptions import (
    FlexCommerceError,
    InsufficientFundsError,
    NotFoundError,
    PaymentError,
    PaymentVerificationError,
    WalletError,
)
from flexcommerce_core.utils.vat import round_price, to_decimal

from . import signals
from .conf import payments_setting
from .gateways import TYPE_INSTANT, get_gateway
from .http import GatewayHTTPError
from .models import Payment, PaymentWebhookLog, Wallet, WalletTransaction

logger = logging.getLogger("flexcommerce.payments")


def payment_payload(payment):
    return {
        "payment_id": str(payment.pk),
        "reference": payment.reference,
        "provider": payment.provider,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "status": payment.status,
        "purpose": payment.purpose,
        "order_number": payment.order.order_number if payment.order_id else None,
    }


def safe_callback_url(url):
    """Allow only configured hosts (open-redirect protection); fall back to the default."""
    default = payments_setting("PAYMENT_CALLBACK_URL") or ""
    if not url:
        return default
    hosts = set(payments_setting("PAYMENT_ALLOWED_CALLBACK_HOSTS") or [])
    if hosts and url_has_allowed_host_and_scheme(url, allowed_hosts=hosts, require_https=False):
        return url
    raise PaymentError("callback_url is not allowed.", code="invalid_callback_url")


class PaymentService:
    @staticmethod
    def start(order, provider, callback_url="", user=None, customer=None) -> Payment:
        """Create a payment for the order's outstanding balance and initiate it."""
        from flexcommerce_orders.models import Order

        if order.status == Order.STATUS_CANCELLED:
            raise PaymentError("This order cannot be paid.", code="order_not_payable", extra={"status": order.status})
        if order.is_paid or order.balance_due <= 0:
            raise PaymentError("This order is already paid.", code="order_already_paid")
        try:
            gateway = get_gateway(provider)
        except KeyError:
            raise PaymentError("Unknown payment method.", code="unknown_payment_method") from None
        payer = user if getattr(user, "is_authenticated", False) else order.user
        if not gateway.is_available(order=order, user=payer):
            raise PaymentError(
                "This payment method is not available for this order.", code="payment_method_unavailable"
            )
        callback = safe_callback_url(callback_url)
        payment = Payment.objects.create(
            order=order,
            user=payer,
            provider=gateway.name,
            amount=order.balance_due,
            currency=order.currency,
            email=order.customer_email,
            callback_url=callback,
        )
        return PaymentService._initiate(
            payment,
            gateway,
            callback,
            customer
            or {
                "name": order.customer_name,
                "phone": order.phone or (order.shipping_address or {}).get("phone", ""),
            },
        )

    @staticmethod
    def _initiate(payment, gateway, callback, customer):
        try:
            result = gateway.initiate(payment, callback_url=callback, customer=customer)
        except GatewayHTTPError as exc:
            result = None
            error = str(exc)
        if result is None or result.status == "failed":
            payment.status = Payment.STATUS_FAILED
            payment.failure_reason = (result.error if result else error)[:500]
            payment.gateway_response = result.raw if result else {}
            payment.save(update_fields=["status", "failure_reason", "gateway_response", "updated_at"])
            emit(
                "payment.failed",
                signal=signals.payment_failed,
                sender=Payment,
                payload=payment_payload(payment),
                payment=payment,
            )
            raise PaymentError(
                payment.failure_reason or "Payment could not be started.", extra={"reference": payment.reference}
            )
        payment.status = Payment.STATUS_PENDING
        payment.authorization_url = result.authorization_url
        payment.provider_reference = result.provider_reference or payment.provider_reference
        payment.gateway_response = {"init": result.raw, "instructions": result.instructions}
        payment.save(
            update_fields=["status", "authorization_url", "provider_reference", "gateway_response", "updated_at"]
        )
        if result.status == "success" and gateway.type == TYPE_INSTANT:
            PaymentService._settle(
                payment.pk, payment.amount, payment.currency, provider_reference=result.provider_reference
            )
            payment.refresh_from_db()
        return payment

    @staticmethod
    @transaction.atomic
    def _settle(payment_id, amount, currency, provider_reference="", channel="", raw=None):
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        if payment.status == Payment.STATUS_SUCCESS:
            return payment
        amount = round_price(to_decimal(amount))
        if currency and currency.upper() != payment.currency.upper():
            return PaymentService._fail(payment, f"Currency mismatch: expected {payment.currency}, got {currency}.")
        if amount < payment.amount:
            return PaymentService._fail(payment, f"Amount mismatch: expected {payment.amount}, received {amount}.")
        payment.status = Payment.STATUS_SUCCESS
        payment.amount_received = amount
        payment.provider_reference = provider_reference or payment.provider_reference
        payment.channel = channel or payment.channel
        payment.verified_at = timezone.now()
        if raw:
            payment.gateway_response = {**payment.gateway_response, "verify": raw}
        payment.save(
            update_fields=[
                "status",
                "amount_received",
                "provider_reference",
                "channel",
                "verified_at",
                "gateway_response",
                "updated_at",
            ]
        )
        if payment.purpose == Payment.PURPOSE_WALLET_TOPUP:
            WalletService.credit(
                payment.user,
                payment.amount,
                reference=f"topup:{payment.reference}",
                source="topup",
                description="Wallet top-up",
            )
        elif payment.order_id:
            from flexcommerce_orders.services import OrderService

            OrderService(payment.order).mark_paid(
                amount=payment.amount, reference=payment.reference, provider=payment.provider, actor=payment.provider
            )
        emit(
            "payment.success",
            signal=signals.payment_succeeded,
            sender=Payment,
            payload=payment_payload(payment),
            payment=payment,
        )
        return payment

    @staticmethod
    def _fail(payment, reason):
        payment.status = Payment.STATUS_FAILED
        payment.failure_reason = reason[:500]
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        logger.warning("Payment %s failed verification: %s", payment.reference, reason)
        if payment.order_id:
            from flexcommerce_orders.services import OrderService

            OrderService(payment.order).mark_payment_failed(reason=reason, actor=payment.provider)
        emit(
            "payment.failed",
            signal=signals.payment_failed,
            sender=Payment,
            payload=payment_payload(payment),
            payment=payment,
        )
        return payment

    @staticmethod
    def verify(reference) -> Payment:
        payment = Payment.objects.select_related("order").filter(reference=reference).first()
        if payment is None:
            raise NotFoundError("Payment not found.", code="payment_not_found")
        if payment.status in (Payment.STATUS_SUCCESS, Payment.STATUS_FAILED):
            return payment
        gateway = get_gateway(payment.provider)
        try:
            result = gateway.verify(payment)
        except GatewayHTTPError as exc:
            raise PaymentVerificationError(str(exc)) from exc
        if result.status == "success":
            return PaymentService._settle(
                payment.pk, result.amount, result.currency, result.provider_reference, result.channel, result.raw
            )
        if result.status == "failed":
            with transaction.atomic():
                locked = Payment.objects.select_for_update().get(pk=payment.pk)
                if locked.status == Payment.STATUS_SUCCESS:
                    return locked
                return PaymentService._fail(locked, result.error or "Payment was not successful.")
        return payment

    @staticmethod
    def handle_webhook(provider, body: bytes, headers) -> PaymentWebhookLog:
        gateway = get_gateway(provider)
        log = PaymentWebhookLog(provider=provider)
        try:
            event = gateway.parse_webhook(body, headers)
        except FlexCommerceError as exc:
            log.error = exc.message
            log.save()
            raise
        log.signature_valid = True
        log.event_id, log.event_type, log.reference, log.payload = (
            event.event_id,
            event.event_type,
            event.reference,
            event.payload,
        )
        duplicate = (
            event.event_id
            and PaymentWebhookLog.objects.filter(provider=provider, event_id=event.event_id, processed=True).exists()
        )
        log.save()
        if duplicate or not event.reference:
            return log
        try:
            PaymentService.verify(event.reference)  # never trust the webhook body: re-verify with the API
            log.processed = True
        except NotFoundError:
            log.error = "Unknown reference"
        except FlexCommerceError as exc:
            log.error = exc.message[:500]
        log.save(update_fields=["processed", "error", "updated_at"])
        return log

    @staticmethod
    def refund_for_order(refund, order, **kwargs):
        """``refund.process`` hook: pay a refund out via the original gateway or the wallet."""
        from flexcommerce_orders.models import Refund

        if refund.method == Refund.METHOD_WALLET:
            if order.user_id is None:
                return {"success": False, "reference": "", "error": "Guest orders cannot be refunded to a wallet."}
            txn = WalletService.credit(
                order.user,
                refund.amount,
                reference=f"refund:{refund.pk}",
                source="refund",
                description=f"Refund for order {order.order_number}",
                order=order,
            )
            return {"success": True, "reference": txn.reference, "error": ""}
        payment = (
            order.payments.filter(status=Payment.STATUS_SUCCESS, purpose=Payment.PURPOSE_ORDER)
            .order_by("-created_at")
            .first()
        )
        if payment is None:
            return None  # paid offline: let the order service treat it as manual
        gateway = get_gateway(payment.provider)
        if not gateway.supports_refunds:
            return None
        try:
            return gateway.refund(payment, refund.amount, refund.reason)
        except GatewayHTTPError as exc:
            return {"success": False, "reference": "", "error": str(exc)}

    @staticmethod
    def expire_stale(hours=24, limit=1000):
        """Job: mark long-pending redirect payments as abandoned (after one last verify)."""
        cutoff = timezone.now() - timedelta(hours=hours)
        stale = Payment.objects.filter(status=Payment.STATUS_PENDING, created_at__lt=cutoff).exclude(
            provider__in=["bank_transfer", "pay_on_delivery"]
        )[:limit]
        abandoned = 0
        for payment in stale:
            try:
                payment = PaymentService.verify(payment.reference)
            except FlexCommerceError:
                pass
            if payment.status == Payment.STATUS_PENDING:
                Payment.objects.filter(pk=payment.pk, status=Payment.STATUS_PENDING).update(
                    status=Payment.STATUS_ABANDONED
                )
                abandoned += 1
        return {"abandoned": abandoned}


class WalletService:
    @staticmethod
    def get_wallet(user, create=True):
        if create:
            wallet, _ = Wallet.objects.get_or_create(user=user)
            return wallet
        return Wallet.objects.filter(user=user).first()

    @staticmethod
    @transaction.atomic
    def credit(user, amount, reference, source="adjustment", description="", order=None) -> WalletTransaction:
        """Add money. Idempotent per ``reference``."""
        amount = round_price(to_decimal(amount))
        if amount <= 0:
            raise WalletError("Amount must be positive.")
        existing = WalletTransaction.objects.filter(reference=reference).first()
        if existing is not None:
            return existing
        wallet = WalletService.get_wallet(user)
        max_balance = payments_setting("WALLET_MAX_BALANCE")
        qs = Wallet.objects.filter(pk=wallet.pk)
        if max_balance is not None and source == "topup":
            qs = qs.filter(balance__lte=to_decimal(max_balance) - amount)
        if not qs.update(balance=F("balance") + amount, updated_at=timezone.now()):
            raise WalletError("Wallet balance limit reached.", code="wallet_limit")
        return WalletService._record(
            wallet, WalletTransaction.TYPE_CREDIT, source, amount, reference, description, order
        )

    @staticmethod
    @transaction.atomic
    def debit(user, amount, reference, source="purchase", description="", order=None) -> WalletTransaction:
        """Take money atomically; never goes negative. Idempotent per ``reference``."""
        amount = round_price(to_decimal(amount))
        if amount <= 0:
            raise WalletError("Amount must be positive.")
        existing = WalletTransaction.objects.filter(reference=reference).first()
        if existing is not None:
            return existing
        wallet = WalletService.get_wallet(user, create=False)
        if wallet is None or not wallet.is_active:
            raise InsufficientFundsError()
        if not Wallet.objects.filter(pk=wallet.pk, is_active=True, balance__gte=amount).update(
            balance=F("balance") - amount, updated_at=timezone.now()
        ):
            wallet.refresh_from_db()
            raise InsufficientFundsError(extra={"balance": str(wallet.balance), "required": str(amount)})
        return WalletService._record(
            wallet, WalletTransaction.TYPE_DEBIT, source, amount, reference, description, order
        )

    @staticmethod
    def _record(wallet, type_, source, amount, reference, description, order):
        wallet.refresh_from_db(fields=["balance"])
        try:
            with transaction.atomic():
                txn = WalletTransaction.objects.create(
                    wallet=wallet,
                    type=type_,
                    source=source,
                    amount=amount,
                    balance_after=wallet.balance,
                    reference=reference,
                    description=description[:255],
                    order=order,
                )
        except IntegrityError:  # same reference raced us: undo our balance change by rolling back
            raise WalletError("Duplicate wallet transaction.", code="duplicate_wallet_transaction") from None
        emit(
            f"wallet.{type_}",
            signal=signals.wallet_transaction,
            sender=WalletTransaction,
            payload={
                "user_id": str(wallet.user_id),
                "amount": str(amount),
                "type": type_,
                "source": source,
                "reference": reference,
                "balance": str(wallet.balance),
            },
            transaction=txn,
            wallet=wallet,
        )
        return txn

    @staticmethod
    def start_topup(user, amount, provider, callback_url="") -> Payment:
        amount = round_price(to_decimal(amount))
        if amount < to_decimal(payments_setting("WALLET_MIN_TOPUP")):
            raise WalletError("Amount is below the minimum top-up.", code="topup_too_small")
        try:
            gateway = get_gateway(provider)
        except KeyError:
            raise PaymentError("Unknown payment method.", code="unknown_payment_method") from None
        if gateway.type != "redirect" or not gateway.is_available(user=user):
            raise PaymentError("This payment method cannot be used for top-ups.", code="payment_method_unavailable")
        from flexcommerce_core.conf import fc_setting

        callback = safe_callback_url(callback_url)
        payment = Payment.objects.create(
            user=user,
            purpose=Payment.PURPOSE_WALLET_TOPUP,
            provider=gateway.name,
            amount=amount,
            currency=fc_setting("CURRENCY", "NGN"),
            email=user.email,
            callback_url=callback,
            reference=f"FCT-{secrets.token_hex(8).upper()}",
        )
        return PaymentService._initiate(payment, gateway, callback, {"name": user.get_full_name(), "phone": ""})


def expire_stale_payments():
    """Job entry point (see ``flexcommerce_run_jobs``)."""
    return PaymentService.expire_stale()
