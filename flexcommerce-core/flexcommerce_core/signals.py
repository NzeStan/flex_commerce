"""
Core-level signals.

``event`` is the generic, JSON-friendly domain event emitted (after commit) for
every business event in every FlexCommerce package. Outgoing webhooks and any
third-party integration can subscribe to it without importing other packages::

    from flexcommerce_core.signals import event

    @receiver(event)
    def forward_to_kafka(sender, name, payload, **kwargs):
        ...
"""

from django.dispatch import Signal

event = Signal()  # kwargs: name (str), payload (dict)
webhook_triggered = Signal()  # kwargs: delivery, success
audit_logged = Signal()  # kwargs: instance, action, actor, entry
