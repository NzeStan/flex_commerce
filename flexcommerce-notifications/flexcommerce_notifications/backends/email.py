"""Email via Django's configured EMAIL_BACKEND (SMTP, SES, SendGrid, Mailgun, Anymail...)."""

import logging
import warnings

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from . import BaseNotificationBackend

logger = logging.getLogger("flexcommerce.notifications.email")


class DjangoEmailBackend(BaseNotificationBackend):
    channel = "email"

    def __init__(self):
        self.from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "noreply@localhost"

    def send(self, recipient, subject, body, **kwargs):
        try:
            message = EmailMultiAlternatives(subject=subject, body=body, from_email=self.from_email, to=[recipient])
            if kwargs.get("html_body"):
                message.attach_alternative(kwargs["html_body"], "text/html")
            message.send(fail_silently=False)
            return self.ok()
        except Exception as exc:  # SMTP / provider errors are retried by the dispatcher
            logger.warning("Email to %s failed: %s", recipient, exc)
            return self.fail(exc)


class TermiiEmailBackend(DjangoEmailBackend):
    """
    Deprecated. An earlier implementation posted message bodies to Termii's *OTP*
    endpoint, which cannot deliver normal emails. It now sends through Django's
    email framework; use ``DjangoEmailBackend`` instead.
    """

    def __init__(self):
        warnings.warn(
            "TermiiEmailBackend is deprecated; use DjangoEmailBackend.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__()
