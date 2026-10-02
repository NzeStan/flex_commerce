"""
Payment gateway interface and registry.

Write your own gateway by subclassing ``BaseGateway`` and registering it::

    FLEXCOMMERCE = {"PAYMENT_GATEWAYS": {**defaults, "stripe": "myapp.payments.StripeGateway"}}

Security rules every gateway follows:
* amounts always come from the server-side ``Payment`` (never the client);
* webhooks are signature-checked, then the payment is *re-verified* with the
  provider's API before an order is marked paid;
* verification checks the amount and currency actually received.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.utils.module_loading import import_string

from ..conf import payments_setting

TYPE_REDIRECT = "redirect"  # customer is sent to the provider's checkout page
TYPE_INSTANT = "instant"  # settled immediately (wallet)
TYPE_OFFLINE = "offline"  # settled outside the system (bank transfer, pay on delivery)


@dataclass
class InitResult:
    status: str = "pending"  # "pending" | "success" | "failed"
    authorization_url: str = ""
    provider_reference: str = ""
    instructions: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class VerifyResult:
    status: str  # "success" | "failed" | "pending"
    amount: Decimal = Decimal("0")
    currency: str = ""
    provider_reference: str = ""
    channel: str = ""
    raw: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class WebhookEvent:
    event_type: str
    reference: str = ""
    event_id: str = ""
    payload: dict = field(default_factory=dict)


class BaseGateway:
    name = ""
    display_name = ""
    type = TYPE_REDIRECT
    supports_refunds = False
    supported_currencies = None  # None = any

    def is_available(self, order=None, user=None) -> bool:
        return True

    def describe(self, order=None) -> dict:
        return {"key": self.name, "label": str(self.display_name or self.name), "type": self.type}

    def initiate(self, payment, callback_url="", customer=None) -> InitResult:
        raise NotImplementedError

    def verify(self, payment) -> VerifyResult:
        raise NotImplementedError

    def parse_webhook(self, body: bytes, headers) -> WebhookEvent:
        """Validate the signature and return the event. Raise ``WebhookSignatureError`` if invalid."""
        raise NotImplementedError

    def refund(self, payment, amount, reason="") -> dict:
        return {"success": False, "reference": "", "error": f"{self.name} does not support automatic refunds."}


def gateway_paths():
    return dict(payments_setting("PAYMENT_GATEWAYS") or {})


def get_gateway(name) -> BaseGateway:
    path = gateway_paths().get(name)
    if not path:
        raise KeyError(name)
    gateway = import_string(path)()
    gateway.name = gateway.name or name
    return gateway


def available_gateways(order=None, user=None):
    result = []
    for name in gateway_paths():
        try:
            gateway = get_gateway(name)
        except (ImportError, KeyError):
            continue
        if gateway.is_available(order=order, user=user):
            result.append(gateway)
    return result


def to_minor_units(amount) -> int:
    """₦1,234.56 → 123456 kobo."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))


def from_minor_units(value) -> Decimal:
    return (Decimal(str(value or 0)) / 100).quantize(Decimal("0.01"))
