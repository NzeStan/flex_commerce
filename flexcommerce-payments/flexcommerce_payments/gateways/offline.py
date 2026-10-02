"""Offline methods settled outside the system and confirmed by staff."""

from django.utils.translation import gettext_lazy as _

from flexcommerce_core.conf import fc_setting
from flexcommerce_core.utils.vat import to_decimal

from ..conf import payments_setting
from . import TYPE_OFFLINE, BaseGateway, InitResult, VerifyResult


class BankTransferGateway(BaseGateway):
    """Customer transfers to your account; staff call ``/orders/{id}/mark-paid/``."""

    name = "bank_transfer"
    display_name = _("Bank transfer")
    type = TYPE_OFFLINE

    def is_available(self, order=None, user=None):
        return bool(payments_setting("BANK_TRANSFER_ACCOUNTS"))

    def _instructions(self, payment=None):
        return {
            "accounts": payments_setting("BANK_TRANSFER_ACCOUNTS"),
            "narration": payment.order.order_number if payment and payment.order_id else "",
            "amount": str(payment.amount) if payment else None,
        }

    def describe(self, order=None):
        return {**super().describe(order), "accounts": payments_setting("BANK_TRANSFER_ACCOUNTS")}

    def initiate(self, payment, callback_url="", customer=None):
        return InitResult(status="pending", instructions=self._instructions(payment))

    def verify(self, payment):
        return VerifyResult(status="pending")


class PayOnDeliveryGateway(BaseGateway):
    """Cash / POS on delivery, with an optional order value limit (``PAY_ON_DELIVERY_LIMIT``)."""

    name = "pay_on_delivery"
    display_name = _("Pay on delivery")
    type = TYPE_OFFLINE

    def limit(self):
        value = fc_setting("PAY_ON_DELIVERY_LIMIT")
        return to_decimal(value) if value not in (None, "") else None

    def is_available(self, order=None, user=None):
        if not payments_setting("PAY_ON_DELIVERY_ENABLED"):
            return False
        limit = self.limit()
        return order is None or limit is None or order.grand_total <= limit

    def describe(self, order=None):
        limit = self.limit()
        return {**super().describe(order), "limit": str(limit) if limit is not None else None}

    def initiate(self, payment, callback_url="", customer=None):
        return InitResult(status="pending", instructions={"message": "Pay the rider on delivery (cash or POS)."})

    def verify(self, payment):
        return VerifyResult(status="pending")
