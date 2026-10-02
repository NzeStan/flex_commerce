"""
Transaction-safe event emission.

Business code calls ``emit`` instead of ``signal.send``. Receivers run only
after the surrounding database transaction commits, so a notification, webhook
or analytics row is never produced for data that was rolled back. Receivers
are called robustly: a failing receiver is logged and never breaks the request
or the other receivers.
"""

import logging

from django.db import transaction

logger = logging.getLogger("flexcommerce.events")


def on_commit(func, using=None):
    """Run ``func`` after the current transaction commits (immediately if none)."""
    transaction.on_commit(func, using=using)


def _send_robust(signal, sender, kwargs):
    for receiver, response in signal.send_robust(sender=sender, **kwargs):
        if isinstance(response, Exception):
            logger.error(
                "FlexCommerce signal receiver %r failed: %s",
                receiver,
                response,
                exc_info=(type(response), response, response.__traceback__),
            )


def emit(name, *, signal=None, sender=None, payload=None, **kwargs):
    """
    Emit a domain event after commit.

    :param name: dotted event name, e.g. ``"order.created"``.
    :param signal: optional package-specific ``django.dispatch.Signal`` to send
        with ``sender`` and ``**kwargs`` (model instances are fine here).
    :param payload: JSON-serialisable dict for the generic ``event`` signal
        (consumed by outgoing webhooks and integrations).
    """
    from .signals import event as generic_event

    def _dispatch():
        if signal is not None:
            _send_robust(signal, sender, kwargs)
        if payload is not None:
            _send_robust(generic_event, sender, {"name": name, "payload": payload})

    on_commit(_dispatch)
