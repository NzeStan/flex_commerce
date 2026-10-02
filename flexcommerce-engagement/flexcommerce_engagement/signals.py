"""Engagement signals (sent after commit)."""

from django.dispatch import Signal

wishlist_item_added = Signal()  # wishlist, item
wishlist_price_drop = Signal()  # item, user, product, old_price, new_price
review_submitted = Signal()  # review
review_approved = Signal()  # review
rating_changed = Signal()  # content_type, object_id, average, count
question_asked = Signal()  # question
question_answered = Signal()  # question, answer
product_viewed = Signal()  # product, user
