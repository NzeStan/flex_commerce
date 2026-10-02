"""
Notification backends. One per channel, swappable in settings::

    FLEXCOMMERCE = {
        "NOTIFICATION_EMAIL_BACKEND": "flexcommerce_notifications.backends.email.DjangoEmailBackend",
        "NOTIFICATION_SMS_BACKEND":   "flexcommerce_notifications.backends.sms.TermiiSMSBackend",
        "NOTIFICATION_PUSH_BACKEND":  "flexcommerce_notifications.backends.push.FCMPushBackend",
    }

A backend implements ``send(recipient, subject, body, **kwargs) -> dict`` returning
``{"success": bool, "message_id": str | None, "error": str | None}`` and should
never raise for provider errors (the dispatcher records and retries failures).
"""

import logging

logger = logging.getLogger("flexcommerce.notifications")


class BaseNotificationBackend:
    channel = None

    def send(self, recipient: str, subject: str, body: str, **kwargs) -> dict:
        raise NotImplementedError

    @staticmethod
    def ok(message_id=None):
        return {"success": True, "message_id": message_id, "error": None}

    @staticmethod
    def fail(error):
        return {"success": False, "message_id": None, "error": str(error)[:1000]}

    def __repr__(self):
        return f"<{self.__class__.__name__} channel={self.channel}>"


class NullBackend(BaseNotificationBackend):
    """Accepts everything and sends nothing (default for SMS / push until configured)."""

    def send(self, recipient, subject, body, **kwargs):
        logger.debug("[NullBackend] would send to %s: %s", recipient, subject[:50])
        return self.ok()


class ConsoleBackend(BaseNotificationBackend):
    """Prints messages to stdout — handy in development."""

    def send(self, recipient, subject, body, **kwargs):
        print(f"--- FlexCommerce notification → {recipient}\n{subject}\n\n{body}\n---")  # noqa: T201
        return self.ok()
