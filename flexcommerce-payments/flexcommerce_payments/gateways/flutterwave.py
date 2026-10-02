"""
Flutterwave v3 (https://flutterwave.com) — cards, bank transfer, USSD, mobile money
across Africa.

Settings: ``FLUTTERWAVE_SECRET_KEY`` (required) and ``FLUTTERWAVE_WEBHOOK_HASH``
(the "secret hash" configured in your Flutterwave dashboard; required for webhooks).
Webhook URL: ``https://<your-domain>/<api-prefix>/payments/webhooks/flutterwave/``
"""

import hmac
import json
from decimal import Decimal
from urllib.parse import quote

from django.utils.translation import gettext_lazy as _

from flexcommerce_core.exceptions import WebhookSignatureError

from ..conf import payments_setting
from ..http import request_json
from . import BaseGateway, InitResult, VerifyResult, WebhookEvent


class FlutterwaveGateway(BaseGateway):
    name = "flutterwave"
    display_name = _("Pay with card, bank or USSD (Flutterwave)")
    supports_refunds = True

    @property
    def secret(self):
        return payments_setting("FLUTTERWAVE_SECRET_KEY")

    def _url(self, path):
        return payments_setting("FLUTTERWAVE_BASE_URL").rstrip("/") + path

    def _headers(self):
        return {"Authorization": f"Bearer {self.secret}"}

    def is_available(self, order=None, user=None):
        return bool(self.secret)

    def describe(self, order=None):
        return {**super().describe(order), "public_key": payments_setting("FLUTTERWAVE_PUBLIC_KEY")}

    def initiate(self, payment, callback_url="", customer=None):
        customer = customer or {}
        payload = {
            "tx_ref": payment.reference,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "redirect_url": callback_url or None,
            "customer": {
                "email": payment.email,
                "phonenumber": customer.get("phone", ""),
                "name": customer.get("name", ""),
            },
            "meta": {
                "order_number": payment.order.order_number if payment.order_id else "",
                "purpose": payment.purpose,
            },
        }
        status, body = request_json("POST", self._url("/payments"), self._headers(), payload)
        data = body.get("data") or {}
        if status == 200 and body.get("status") == "success" and data.get("link"):
            return InitResult(authorization_url=data["link"], raw={"link": data["link"]})
        return InitResult(status="failed", error=str(body.get("message") or f"HTTP {status}"), raw=body)

    def verify(self, payment):
        url = self._url(f"/transactions/verify_by_reference?tx_ref={quote(payment.reference)}")
        status, body = request_json("GET", url, self._headers())
        data = body.get("data") or {}
        if status != 200 or body.get("status") != "success":
            return VerifyResult(
                status="pending" if status >= 500 else "failed",
                error=str(body.get("message") or f"HTTP {status}"),
                raw=body,
            )
        gateway_status = data.get("status")
        mapped = (
            "success" if gateway_status == "successful" else ("failed" if gateway_status == "failed" else "pending")
        )
        return VerifyResult(
            status=mapped,
            amount=Decimal(str(data.get("amount") or 0)).quantize(Decimal("0.01")),
            currency=data.get("currency", ""),
            provider_reference=str(data.get("id", "")),
            channel=data.get("payment_type", ""),
            raw={k: data.get(k) for k in ("id", "tx_ref", "status", "amount", "currency", "payment_type")},
            error="" if mapped == "success" else str(data.get("processor_response") or gateway_status),
        )

    def parse_webhook(self, body, headers):
        expected = payments_setting("FLUTTERWAVE_WEBHOOK_HASH")
        signature = headers.get("verif-hash") or headers.get("Verif-Hash") or ""
        if not expected or not signature or not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError()
        payload = json.loads(body or b"{}")
        data = payload.get("data") or {}
        return WebhookEvent(
            event_type=payload.get("event") or payload.get("event.type", ""),
            reference=str(data.get("tx_ref") or data.get("txRef") or ""),
            event_id=f"{payload.get('event', '')}:{data.get('id', '')}",
            payload={"event": payload.get("event"), "tx_ref": data.get("tx_ref"), "status": data.get("status")},
        )

    def refund(self, payment, amount, reason=""):
        if not payment.provider_reference:
            return {"success": False, "reference": "", "error": "Missing Flutterwave transaction id."}
        status, body = request_json(
            "POST",
            self._url(f"/transactions/{payment.provider_reference}/refund"),
            self._headers(),
            {"amount": str(amount)},
        )
        data = body.get("data") or {}
        if status == 200 and body.get("status") == "success":
            return {"success": True, "reference": str(data.get("id", "")), "error": ""}
        return {"success": False, "reference": "", "error": str(body.get("message") or f"HTTP {status}")}
