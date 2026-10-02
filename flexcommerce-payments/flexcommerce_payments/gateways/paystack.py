"""
Paystack (https://paystack.com) — cards, bank transfer, USSD, QR, mobile money.

Settings: ``PAYSTACK_SECRET_KEY`` (required), ``PAYSTACK_PUBLIC_KEY``.
Webhook URL to register in the Paystack dashboard:
``https://<your-domain>/<api-prefix>/payments/webhooks/paystack/``
"""

import hashlib
import hmac
import json

from django.utils.translation import gettext_lazy as _

from flexcommerce_core.exceptions import WebhookSignatureError

from ..conf import payments_setting
from ..http import request_json
from . import BaseGateway, InitResult, VerifyResult, WebhookEvent, from_minor_units, to_minor_units


class PaystackGateway(BaseGateway):
    name = "paystack"
    display_name = _("Pay with card, bank or USSD (Paystack)")
    supports_refunds = True
    supported_currencies = {"NGN", "GHS", "ZAR", "KES", "USD", "XOF", "EGP"}

    @property
    def secret(self):
        return payments_setting("PAYSTACK_SECRET_KEY")

    def _url(self, path):
        return payments_setting("PAYSTACK_BASE_URL").rstrip("/") + path

    def _headers(self):
        return {"Authorization": f"Bearer {self.secret}"}

    def is_available(self, order=None, user=None):
        if not self.secret:
            return False
        return order is None or order.currency in self.supported_currencies

    def describe(self, order=None):
        return {**super().describe(order), "public_key": payments_setting("PAYSTACK_PUBLIC_KEY")}

    def initiate(self, payment, callback_url="", customer=None):
        payload = {
            "email": payment.email,
            "amount": to_minor_units(payment.amount),
            "currency": payment.currency,
            "reference": payment.reference,
            "metadata": {
                "order_number": payment.order.order_number if payment.order_id else "",
                "purpose": payment.purpose,
                "cancel_action": callback_url or "",
            },
        }
        if callback_url:
            payload["callback_url"] = callback_url
        status, body = request_json("POST", self._url("/transaction/initialize"), self._headers(), payload)
        data = body.get("data") or {}
        if status == 200 and body.get("status") and data.get("authorization_url"):
            return InitResult(
                authorization_url=data["authorization_url"], provider_reference=data.get("access_code", ""), raw=data
            )
        return InitResult(status="failed", error=str(body.get("message") or f"HTTP {status}"), raw=body)

    def verify(self, payment):
        status, body = request_json("GET", self._url(f"/transaction/verify/{payment.reference}"), self._headers())
        data = body.get("data") or {}
        if status != 200 or not body.get("status"):
            return VerifyResult(
                status="pending" if status >= 500 else "failed",
                error=str(body.get("message") or f"HTTP {status}"),
                raw=body,
            )
        gateway_status = data.get("status")
        mapped = (
            "success"
            if gateway_status == "success"
            else ("failed" if gateway_status in ("failed", "reversed", "abandoned") else "pending")
        )
        return VerifyResult(
            status=mapped,
            amount=from_minor_units(data.get("amount")),
            currency=data.get("currency", ""),
            provider_reference=str(data.get("id", "")),
            channel=data.get("channel", ""),
            raw={
                k: data.get(k)
                for k in ("id", "status", "reference", "amount", "currency", "channel", "gateway_response", "paid_at")
            },
            error="" if mapped == "success" else str(data.get("gateway_response") or gateway_status),
        )

    def parse_webhook(self, body, headers):
        signature = headers.get("X-Paystack-Signature") or headers.get("x-paystack-signature") or ""
        expected = hmac.new(self.secret.encode(), body, hashlib.sha512).hexdigest()
        if not self.secret or not signature or not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError()
        payload = json.loads(body or b"{}")
        data = payload.get("data") or {}
        return WebhookEvent(
            event_type=payload.get("event", ""),
            reference=str(data.get("reference", "")),
            event_id=f"{payload.get('event', '')}:{data.get('id', '')}",
            payload={"event": payload.get("event"), "reference": data.get("reference"), "status": data.get("status")},
        )

    def refund(self, payment, amount, reason=""):
        payload = {"transaction": payment.reference, "amount": to_minor_units(amount)}
        if reason:
            payload["merchant_note"] = reason[:200]
        status, body = request_json("POST", self._url("/refund"), self._headers(), payload)
        data = body.get("data") or {}
        if status in (200, 201) and body.get("status"):
            return {"success": True, "reference": str(data.get("id", "")), "error": ""}
        return {"success": False, "reference": "", "error": str(body.get("message") or f"HTTP {status}")}
