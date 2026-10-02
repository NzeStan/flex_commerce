"""
Order signals — all sent *after commit* through ``flexcommerce_core.events.emit``
(the same events are also available as JSON webhooks).

Every order signal passes ``order``; transition signals also pass
``from_state`` and ``actor``.
"""

from django.dispatch import Signal

order_created = Signal()
order_confirmed = Signal()  # committed: paid, pay-on-delivery accepted, or staff-confirmed
order_paid = Signal()
order_payment_failed = Signal()
order_processing = Signal()
order_partially_shipped = Signal()
order_shipped = Signal()
order_delivered = Signal()
order_cancelled = Signal()
order_refunded = Signal()
order_partially_refunded = Signal()
shipment_created = Signal()  # shipment, order
shipment_updated = Signal()  # shipment, order, event
refund_requested = Signal()  # refund, order
refund_processed = Signal()  # refund, order
refund_failed = Signal()  # refund, order
return_requested = Signal()  # return_request, order
return_updated = Signal()  # return_request, order
