"""Payment signals (sent after commit)."""

from django.dispatch import Signal

payment_succeeded = Signal()  # payment
payment_failed = Signal()  # payment
wallet_transaction = Signal()  # transaction, wallet
