"""
FlexCommerce Payments.

* ``Payment``            – one attempt to collect money for an order (or a wallet top-up)
* ``PaymentWebhookLog``  – every inbound gateway webhook (dedupe + audit)
* ``Wallet`` / ``WalletTransaction`` – customer store credit with an append-only,
  idempotent ledger (refunds to wallet, top-ups, purchases, adjustments)
"""

import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel

ZERO = Decimal("0.00")


def new_reference(prefix="FCP"):
    return f"{prefix}-{secrets.token_hex(8).upper()}"


class Payment(TimeStampedUUIDModel):
    STATUS_INITIATED = "initiated"
    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_ABANDONED = "abandoned"
    STATUS_CHOICES = [
        (STATUS_INITIATED, _("Initiated")),
        (STATUS_PENDING, _("Pending")),
        (STATUS_SUCCESS, _("Successful")),
        (STATUS_FAILED, _("Failed")),
        (STATUS_ABANDONED, _("Abandoned")),
    ]
    PURPOSE_ORDER = "order"
    PURPOSE_WALLET_TOPUP = "wallet_topup"
    PURPOSE_CHOICES = [(PURPOSE_ORDER, _("Order")), (PURPOSE_WALLET_TOPUP, _("Wallet top-up"))]

    order = models.ForeignKey(
        "flexcommerce_orders.Order", on_delete=models.PROTECT, null=True, blank=True, related_name="payments"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments"
    )
    purpose = models.CharField(_("purpose"), max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_ORDER)
    provider = models.CharField(_("provider"), max_length=50, db_index=True)
    reference = models.CharField(_("reference"), max_length=100, unique=True, default=new_reference)
    provider_reference = models.CharField(_("provider reference"), max_length=200, blank=True, db_index=True)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    amount_received = models.DecimalField(_("amount received"), max_digits=14, decimal_places=2, default=ZERO)
    currency = models.CharField(_("currency"), max_length=10, default="NGN")
    status = models.CharField(
        _("status"), max_length=20, choices=STATUS_CHOICES, default=STATUS_INITIATED, db_index=True
    )
    channel = models.CharField(_("channel"), max_length=50, blank=True, help_text=_("card, bank, ussd, transfer..."))
    email = models.EmailField(_("payer email"), blank=True)
    authorization_url = models.URLField(_("authorization URL"), max_length=1000, blank=True)
    callback_url = models.URLField(_("callback URL"), max_length=1000, blank=True)
    failure_reason = models.CharField(_("failure reason"), max_length=500, blank=True)
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)
    gateway_response = models.JSONField(_("gateway response"), default=dict, blank=True)

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["order", "status"], name="payments_order_status_idx")]

    def __str__(self):
        return f"{self.provider} {self.reference} {self.amount} {self.currency} [{self.status}]"

    @property
    def is_successful(self):
        return self.status == self.STATUS_SUCCESS


class PaymentWebhookLog(TimeStampedUUIDModel):
    provider = models.CharField(_("provider"), max_length=50)
    event_id = models.CharField(_("event ID"), max_length=200, blank=True)
    event_type = models.CharField(_("event type"), max_length=100, blank=True)
    reference = models.CharField(_("reference"), max_length=200, blank=True, db_index=True)
    signature_valid = models.BooleanField(_("signature valid"), default=False)
    processed = models.BooleanField(_("processed"), default=False)
    error = models.CharField(_("error"), max_length=500, blank=True)
    payload = models.JSONField(_("payload"), default=dict, blank=True)

    class Meta:
        verbose_name = _("payment webhook")
        verbose_name_plural = _("payment webhooks")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["provider", "event_id"], name="payments_webhook_event_idx")]

    def __str__(self):
        return f"{self.provider} {self.event_type} {self.reference}"


class Wallet(TimeStampedUUIDModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(_("balance"), max_digits=14, decimal_places=2, default=ZERO)
    currency = models.CharField(_("currency"), max_length=10, default="NGN")
    is_active = models.BooleanField(_("is active"), default=True)

    class Meta:
        verbose_name = _("wallet")
        verbose_name_plural = _("wallets")

    def __str__(self):
        return f"Wallet({self.user}): {self.balance} {self.currency}"


class WalletTransaction(TimeStampedUUIDModel):
    TYPE_CREDIT = "credit"
    TYPE_DEBIT = "debit"
    TYPE_CHOICES = [(TYPE_CREDIT, _("Credit")), (TYPE_DEBIT, _("Debit"))]
    SOURCE_CHOICES = [
        ("topup", _("Top-up")),
        ("refund", _("Refund")),
        ("purchase", _("Purchase")),
        ("adjustment", _("Adjustment")),
        ("cashback", _("Cashback / reward")),
        ("reversal", _("Reversal")),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    type = models.CharField(_("type"), max_length=10, choices=TYPE_CHOICES)
    source = models.CharField(_("source"), max_length=20, choices=SOURCE_CHOICES)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(_("balance after"), max_digits=14, decimal_places=2)
    reference = models.CharField(_("reference"), max_length=150, unique=True)
    description = models.CharField(_("description"), max_length=255, blank=True)
    order = models.ForeignKey(
        "flexcommerce_orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transactions",
    )

    class Meta:
        verbose_name = _("wallet transaction")
        verbose_name_plural = _("wallet transactions")
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.type == self.TYPE_CREDIT else "-"
        return f"{sign}{self.amount} ({self.source}) → {self.balance_after}"
