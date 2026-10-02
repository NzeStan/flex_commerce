"""Pay from the customer's FlexCommerce wallet (store credit)."""

from django.utils.translation import gettext_lazy as _

from flexcommerce_core.exceptions import FlexCommerceError

from ..conf import payments_setting
from . import TYPE_INSTANT, BaseGateway, InitResult, VerifyResult


class WalletGateway(BaseGateway):
    name = "wallet"
    display_name = _("Wallet balance")
    type = TYPE_INSTANT
    supports_refunds = True

    def is_available(self, order=None, user=None):
        if not payments_setting("WALLET_ENABLED"):
            return False
        user = user or (order.user if order is not None and order.user_id else None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        from ..models import Wallet

        wallet = Wallet.objects.filter(user=user, is_active=True).first()
        return wallet is not None and (order is None or wallet.balance >= order.balance_due)

    def initiate(self, payment, callback_url="", customer=None):
        from ..services import WalletService

        if payment.user is None:
            return InitResult(status="failed", error="Sign in to pay with your wallet.")
        try:
            WalletService.debit(
                payment.user,
                payment.amount,
                reference=f"pay:{payment.reference}",
                source="purchase",
                description=f"Order {payment.order.order_number}" if payment.order_id else "Payment",
                order=payment.order,
            )
        except FlexCommerceError as exc:
            return InitResult(status="failed", error=exc.message)
        return InitResult(status="success", provider_reference=f"pay:{payment.reference}")

    def verify(self, payment):
        from ..models import WalletTransaction

        exists = WalletTransaction.objects.filter(reference=f"pay:{payment.reference}").exists()
        return VerifyResult(status="success" if exists else "failed", amount=payment.amount, currency=payment.currency)

    def refund(self, payment, amount, reason=""):
        from ..services import WalletService

        txn = WalletService.credit(
            payment.user,
            amount,
            reference=f"refund:{payment.reference}:{amount}",
            source="refund",
            description=reason or "Refund",
            order=payment.order,
        )
        return {"success": True, "reference": txn.reference, "error": ""}
