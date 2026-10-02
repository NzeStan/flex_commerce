"""Engagement services: product resolution, reviews, ratings, wishlist and history."""

import logging
import secrets

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, F, Q
from django.db.models.functions import Greatest
from django.utils import timezone

from flexcommerce_core.events import emit
from flexcommerce_core.exceptions import (
    FlexCommerceError,
    InvalidProductTypeError,
    PermissionDeniedError,
    ProductNotFoundError,
)
from flexcommerce_core.utils.products import get_product_model_paths, is_installed, load_model
from flexcommerce_core.utils.vat import round_price, to_decimal

from . import signals
from .conf import engagement_setting
from .models import (
    ProductReview,
    RecentlyViewed,
    RecentSearch,
    ReviewVote,
    SearchTerm,
    Wishlist,
    WishlistItem,
)

# ── Products ─────────────────────────────────────────────────────────────────


def engagement_model_paths():
    paths = engagement_setting("ENGAGEMENT_MODELS")
    if paths is not None:
        return [paths] if isinstance(paths, str) else list(paths)
    result = ["flexcommerce_catalog.Product"] if is_installed("flexcommerce_catalog") else []
    return result + [p for p in get_product_model_paths() if p not in result]


def resolve_product(product_type, product_id):
    """A product customers may review / wishlist (whitelisted models only)."""
    models = [load_model(p) for p in engagement_model_paths()]
    if not models:
        raise InvalidProductTypeError("No product models are configured.")
    if product_type:
        wanted = str(product_type).strip().lower()
        models = [m for m in models if wanted in (m._meta.label_lower, m._meta.model_name)]
        if not models:
            raise InvalidProductTypeError(extra={"product_type": product_type})
    for model in models:  # ids are UUIDs, so trying each allowed model is unambiguous
        try:
            return model._default_manager.get(pk=product_id)
        except Exception:  # noqa: S112 - not this model / malformed id
            continue
    raise ProductNotFoundError(extra={"product_id": str(product_id)})


def product_ref(product):
    return ContentType.objects.get_for_model(product), product.pk


# ── Reviews ──────────────────────────────────────────────────────────────────


def has_purchased(user, product) -> bool:
    if not is_installed("flexcommerce_orders") or not user.is_authenticated:
        return False
    from flexcommerce_orders.models import Order, OrderItem

    ids = {product.pk}
    purchasable = getattr(product, "purchasable_ids", None)
    if callable(purchasable):
        ids.update(purchasable())
    return OrderItem.objects.filter(
        Q(object_id__in=ids) | Q(parent_product_id=product.pk),
        order__user=user,
        order__status__in=[
            Order.STATUS_CONFIRMED,
            Order.STATUS_PROCESSING,
            Order.STATUS_PARTIALLY_SHIPPED,
            Order.STATUS_SHIPPED,
            Order.STATUS_DELIVERED,
            Order.STATUS_PARTIALLY_REFUNDED,
        ],
    ).exists()


def rating_summary(content_type, object_id) -> dict:
    qs = ProductReview.objects.filter(content_type=content_type, object_id=object_id, is_approved=True)
    agg = qs.aggregate(avg=Avg("rating"), count=Count("id"))
    distribution = {str(i): 0 for i in range(1, 6)}
    for row in qs.values("rating").annotate(n=Count("id")):
        distribution[str(row["rating"])] = row["n"]
    average = round_price(to_decimal(agg["avg"] or 0))
    return {"average": average, "count": agg["count"], "distribution": distribution}


def refresh_rating(content_type, object_id):
    summary = rating_summary(content_type, object_id)
    emit(
        "review.rating_changed",
        signal=signals.rating_changed,
        sender=ProductReview,
        content_type=content_type,
        object_id=object_id,
        average=summary["average"],
        count=summary["count"],
    )
    return summary


@transaction.atomic
def create_review(user, product, rating, body, title="", image_urls=None):
    verified = has_purchased(user, product)
    if engagement_setting("REVIEWS_VERIFIED_ONLY") and not verified:
        raise PermissionDeniedError("Only customers who bought this product can review it.", code="purchase_required")
    ct, pk = product_ref(product)
    try:
        with transaction.atomic():
            review = ProductReview.objects.create(
                content_type=ct,
                object_id=pk,
                user=user,
                rating=rating,
                body=body,
                title=title,
                image_urls=image_urls or [],
                is_verified_purchase=verified,
                is_approved=not engagement_setting("REVIEWS_REQUIRE_APPROVAL"),
            )
    except IntegrityError:
        raise FlexCommerceError("You have already reviewed this product.", code="already_reviewed") from None
    emit(
        "review.submitted",
        signal=signals.review_submitted,
        sender=ProductReview,
        payload={"review_id": str(review.pk), "product_id": str(pk), "rating": rating},
        review=review,
    )
    if review.is_approved:
        refresh_rating(ct, pk)
    return review


@transaction.atomic
def update_review(review, **changes):
    was_approved = review.is_approved
    for key in ("rating", "title", "body", "image_urls"):
        if key in changes:
            setattr(review, key, changes[key])
    if engagement_setting("REVIEWS_REQUIRE_APPROVAL"):
        review.is_approved = False
        review.is_rejected = False
    review.save()
    if was_approved or review.is_approved:
        refresh_rating(review.content_type, review.object_id)
    return review


@transaction.atomic
def moderate_review(review, approve: bool, note=""):
    review.is_approved = approve
    review.is_rejected = not approve
    review.moderation_note = note[:255]
    review.save(update_fields=["is_approved", "is_rejected", "moderation_note", "updated_at"])
    if approve:
        emit("review.approved", signal=signals.review_approved, sender=ProductReview, review=review)
    refresh_rating(review.content_type, review.object_id)
    return review


@transaction.atomic
def delete_review(review):
    ct, pk, approved = review.content_type, review.object_id, review.is_approved
    review.delete()
    if approved:
        refresh_rating(ct, pk)


@transaction.atomic
def toggle_helpful(review, user) -> bool:
    """Vote / un-vote a review as helpful. Returns True if the user now has a vote."""
    if review.user_id == user.pk:
        raise PermissionDeniedError("You cannot vote on your own review.")
    deleted, _ = ReviewVote.objects.filter(review=review, user=user).delete()
    if deleted:
        ProductReview.objects.filter(pk=review.pk).update(helpful_count=Greatest(F("helpful_count") - 1, 0))
        return False
    try:
        with transaction.atomic():
            ReviewVote.objects.create(review=review, user=user)
    except IntegrityError:
        return True
    ProductReview.objects.filter(pk=review.pk).update(helpful_count=F("helpful_count") + 1)
    return True


# ── Wishlist ─────────────────────────────────────────────────────────────────


def default_wishlist(user):
    wishlist = Wishlist.objects.filter(user=user, is_default=True).first()
    if wishlist is not None:
        return wishlist
    try:
        with transaction.atomic():
            return Wishlist.objects.create(user=user, is_default=True, name="My Wishlist")
    except IntegrityError:
        return Wishlist.objects.get(user=user, is_default=True)


def current_price(product):
    try:
        from flexcommerce_core.utils.pricing import get_unit_price

        return get_unit_price(product)
    except Exception:
        price = getattr(product, "price", None) or getattr(product, "min_price", None)
        return to_decimal(price) if price is not None else None


def _display_price(product):
    price = getattr(product, "min_price", None)
    if price is not None and not hasattr(product, "price"):
        return to_decimal(price)
    return current_price(product)


def add_to_wishlist(wishlist, product, note=""):
    ct, pk = product_ref(product)
    item, created = WishlistItem.objects.get_or_create(
        wishlist=wishlist,
        content_type=ct,
        object_id=pk,
        defaults={"note": note, "added_price": _display_price(product)},
    )
    if created:
        emit(
            "wishlist.item_added",
            signal=signals.wishlist_item_added,
            sender=Wishlist,
            wishlist=wishlist,
            item=item,
        )
    return item, created


def toggle_wishlist(user, product):
    wishlist = default_wishlist(user)
    ct, pk = product_ref(product)
    deleted, _ = WishlistItem.objects.filter(wishlist__user=user, content_type=ct, object_id=pk).delete()
    if deleted:
        return None
    item, _ = add_to_wishlist(wishlist, product)
    return item


def purchasable_for(product):
    """The purchasable row for a wishlist product (catalog Product → default variant)."""
    from flexcommerce_core.utils.products import is_product_instance

    if is_product_instance(product):
        return product
    default = getattr(product, "default_variant", None)
    if default is not None and is_product_instance(default):
        return default
    return None


def detect_price_drops(limit=5000):
    """Job: emit ``wishlist_price_drop`` when a wishlisted product got cheaper."""
    threshold = to_decimal(engagement_setting("WISHLIST_PRICE_DROP_PERCENT"))
    items = WishlistItem.objects.filter(
        added_price__isnull=False, price_alert_sent_at__isnull=True, wishlist__user__isnull=False
    ).select_related("wishlist__user")[:limit]
    sent = 0
    for item in items:
        product = item.product
        if product is None:
            continue
        price = _display_price(product)
        if price is None or item.added_price <= 0:
            continue
        drop = (item.added_price - price) / item.added_price * 100
        if drop >= threshold:
            WishlistItem.objects.filter(pk=item.pk).update(price_alert_sent_at=timezone.now())
            emit(
                "wishlist.price_drop",
                signal=signals.wishlist_price_drop,
                sender=WishlistItem,
                item=item,
                user=item.wishlist.user,
                product=product,
                old_price=item.added_price,
                new_price=price,
            )
            sent += 1
    return {"alerts": sent}


# ── Browsing & search history ────────────────────────────────────────────────


VISITOR_KEY = "flexcommerce_visitor"


def _session_key(request, create=False):
    """Anonymous visitor id kept *inside* the session data, so it survives the
    session-key rotation Django performs at login (needed to merge history)."""
    session = getattr(request, "session", None)
    if session is None:
        return ""
    key = session.get(VISITOR_KEY, "")
    if not key and create:
        key = secrets.token_hex(16)
        session[VISITOR_KEY] = key
    return key


def _owner(request, create=False):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return {"user": user}
    key = _session_key(request, create=create)
    return {"session_key": key, "user__isnull": True} if key else None


def track_view(request, product):
    owner = _owner(request, create=True)
    if owner is None:
        return None
    ct, pk = product_ref(product)
    lookup = {k: v for k, v in owner.items() if k != "user__isnull"}
    updated = RecentlyViewed.objects.filter(**owner, content_type=ct, object_id=pk).update(
        view_count=F("view_count") + 1, updated_at=timezone.now()
    )
    if not updated:
        RecentlyViewed.objects.create(**lookup, content_type=ct, object_id=pk)
    limit = engagement_setting("RECENTLY_VIEWED_LIMIT")
    stale = list(RecentlyViewed.objects.filter(**owner).order_by("-updated_at").values_list("pk", flat=True)[limit:])
    if stale:
        RecentlyViewed.objects.filter(pk__in=stale).delete()
    emit(
        "product.viewed",
        signal=signals.product_viewed,
        sender=RecentlyViewed,
        product=product,
        user=owner.get("user"),
    )
    return True


def record_search(request, query):
    query = " ".join(str(query or "").split())[:255]
    if not query:
        return None
    term = query.lower()
    if not SearchTerm.objects.filter(term=term).update(count=F("count") + 1, updated_at=timezone.now()):
        try:
            with transaction.atomic():
                SearchTerm.objects.create(term=term, count=1)
        except IntegrityError:
            SearchTerm.objects.filter(term=term).update(count=F("count") + 1)
    owner = _owner(request, create=False)
    if owner is None:
        return None
    lookup = {k: v for k, v in owner.items() if k != "user__isnull"}
    if not RecentSearch.objects.filter(**owner, query__iexact=query).update(updated_at=timezone.now(), query=query):
        RecentSearch.objects.create(**lookup, query=query)
    limit = engagement_setting("RECENT_SEARCHES_LIMIT")
    stale = list(RecentSearch.objects.filter(**owner).order_by("-updated_at").values_list("pk", flat=True)[limit:])
    if stale:
        RecentSearch.objects.filter(pk__in=stale).delete()
    return query


def merge_anonymous_history(request, user):
    """On login, attach the visitor's session history to the account."""
    key = _session_key(request)
    if not key:
        return
    for rv in RecentlyViewed.objects.filter(session_key=key, user__isnull=True):
        if RecentlyViewed.objects.filter(user=user, content_type=rv.content_type, object_id=rv.object_id).exists():
            rv.delete()
        else:
            RecentlyViewed.objects.filter(pk=rv.pk).update(user=user, session_key="")
    RecentSearch.objects.filter(session_key=key, user__isnull=True).update(user=user, session_key="")


def on_user_logged_in(sender, request, user, **kwargs):
    if request is not None:
        try:
            merge_anonymous_history(request, user)
        except Exception:  # never break login
            logging.getLogger("flexcommerce.engagement").exception("history merge failed")
