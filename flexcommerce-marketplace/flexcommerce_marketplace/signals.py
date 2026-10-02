"""Marketplace signals (sent after commit)."""

from django.dispatch import Signal

vendor_applied = Signal()  # vendor
vendor_approved = Signal()  # vendor
vendor_status_changed = Signal()  # vendor, status
vendor_order_created = Signal()  # vendor_order
payout_created = Signal()  # payout
payout_updated = Signal()  # payout
