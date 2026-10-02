"""
SMS backends: Termii, Africa's Talking, Twilio and Kudisms.

All phone numbers are normalised to international format (``0803…`` → ``234803…``).
Provider responses are tested with mocked HTTP; confirm sender IDs and
endpoints against your provider account before going live.
"""

import logging

from flexcommerce_core.conf import fc_setting
from flexcommerce_core.http import HTTPClientError, request_json
from flexcommerce_core.validators import normalize_phone

from . import BaseNotificationBackend

logger = logging.getLogger("flexcommerce.notifications.sms")


class TermiiSMSBackend(BaseNotificationBackend):
    """
    Termii (https://termii.com). Settings: ``TERMII_API_KEY``, ``TERMII_SENDER_ID``
    (≤ 11 chars, approved by Termii), ``TERMII_BASE_URL`` (your account's base URL,
    shown in the Termii dashboard), ``TERMII_CHANNEL`` ("generic" or "dnd" for
    delivery to DND numbers).
    """

    channel = "sms"

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        return normalize_phone(phone)

    def send(self, recipient, subject, body, **kwargs):
        api_key = fc_setting("TERMII_API_KEY", "")
        if not api_key:
            return self.fail("TERMII_API_KEY is not configured")
        base = (fc_setting("TERMII_BASE_URL", "") or "https://api.ng.termii.com").rstrip("/")
        payload = {
            "to": normalize_phone(recipient),
            "from": fc_setting("TERMII_SENDER_ID", "") or "FlexCommerce",
            "sms": body,
            "type": "plain",
            "api_key": api_key,
            "channel": fc_setting("TERMII_CHANNEL", "") or "generic",
        }
        try:
            status, data = request_json("POST", f"{base}/api/sms/send", payload=payload, timeout=15)
        except HTTPClientError as exc:
            return self.fail(exc)
        if status == 200 and (data.get("code") == "ok" or data.get("message_id")):
            return self.ok(data.get("message_id"))
        return self.fail(data.get("message") or data)


class AfricasTalkingSMSBackend(BaseNotificationBackend):
    """Africa's Talking. Settings: ``AT_USERNAME``, ``AT_API_KEY``, ``AT_SENDER_ID`` (optional)."""

    channel = "sms"

    def send(self, recipient, subject, body, **kwargs):
        username, api_key = fc_setting("AT_USERNAME", ""), fc_setting("AT_API_KEY", "")
        if not username or not api_key:
            return self.fail("Africa's Talking credentials are not configured")
        form = {"username": username, "to": "+" + normalize_phone(recipient), "message": body}
        if fc_setting("AT_SENDER_ID", None):
            form["from"] = fc_setting("AT_SENDER_ID")
        host = "api.sandbox.africastalking.com" if username == "sandbox" else "api.africastalking.com"
        try:
            status, data = request_json(
                "POST",
                f"https://{host}/version1/messaging",
                form=form,
                headers={"apiKey": api_key},
                timeout=15,
            )
        except HTTPClientError as exc:
            return self.fail(exc)
        recipients = (data.get("SMSMessageData") or {}).get("Recipients") or [{}]
        entry = recipients[0]
        if status in (200, 201) and entry.get("status") == "Success":
            return self.ok(entry.get("messageId"))
        return self.fail(entry.get("status") or data)


class TwilioSMSBackend(BaseNotificationBackend):
    """Twilio. Settings: ``TWILIO_ACCOUNT_SID``, ``TWILIO_AUTH_TOKEN``, ``TWILIO_FROM``."""

    channel = "sms"

    def send(self, recipient, subject, body, **kwargs):
        sid, token, sender = (
            fc_setting("TWILIO_ACCOUNT_SID", ""),
            fc_setting("TWILIO_AUTH_TOKEN", ""),
            fc_setting("TWILIO_FROM", ""),
        )
        if not sid or not token or not sender:
            return self.fail("Twilio credentials are not configured")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        try:
            status, data = request_json(
                "POST",
                url,
                form={"To": "+" + normalize_phone(recipient), "From": sender, "Body": body},
                auth=(sid, token),
                timeout=15,
            )
        except HTTPClientError as exc:
            return self.fail(exc)
        if status in (200, 201) and data.get("sid"):
            return self.ok(data["sid"])
        return self.fail(data.get("message") or data)


class KudismsSMSBackend(BaseNotificationBackend):
    """
    Kudisms (https://kudisms.net). Settings: ``KUDISMS_TOKEN``, ``KUDISMS_SENDER_ID``
    and optionally ``KUDISMS_API_URL``. Credentials are sent in the POST body,
    never in the URL.
    """

    channel = "sms"

    def send(self, recipient, subject, body, **kwargs):
        token = fc_setting("KUDISMS_TOKEN", "")
        if not token:
            return self.fail("KUDISMS_TOKEN is not configured")
        url = fc_setting("KUDISMS_API_URL", "") or "https://my.kudisms.net/api/sms"
        form = {
            "token": token,
            "senderID": fc_setting("KUDISMS_SENDER_ID", "") or "FlexCommerce",
            "recipients": normalize_phone(recipient),
            "message": body,
        }
        try:
            status, data = request_json("POST", url, form=form, timeout=15)
        except HTTPClientError as exc:
            return self.fail(exc)
        if status == 200 and str(data.get("status", "")).lower() in ("success", "ok", "200"):
            return self.ok(data.get("message_id") or data.get("data"))
        return self.fail(data.get("msg") or data.get("message") or data)
