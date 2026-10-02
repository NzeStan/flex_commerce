"""Cart signals (sent after commit)."""

from django.dispatch import Signal

item_added = Signal()  # cart, item, created
item_updated = Signal()  # cart, item
item_removed = Signal()  # cart, item
cart_cleared = Signal()  # cart
cart_merged = Signal()  # cart, merged_from
cart_abandoned = Signal()  # cart
cart_recovered = Signal()  # cart, order  (an abandoned cart was checked out)
cart_expired = Signal()  # cart
cart_locked = Signal()  # cart
cart_unlocked = Signal()  # cart
coupon_applied = Signal()  # cart, code, discount
coupon_removed = Signal()  # cart
