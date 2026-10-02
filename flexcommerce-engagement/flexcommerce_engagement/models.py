"""
FlexCommerce Engagement: wishlists, reviews & ratings, product Q&A, recently
viewed products and search history / trending searches.
"""

import secrets

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from flexcommerce_core.models import TimeStampedUUIDModel

USER = settings.AUTH_USER_MODEL


class ProductRefMixin(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField(_("product ID"))
    product = GenericForeignKey("content_type", "object_id")

    class Meta:
        abstract = True


# ── Wishlists ─────────────────────────────────────────────────────────────────


class Wishlist(TimeStampedUUIDModel):
    VISIBILITY_PRIVATE = "private"
    VISIBILITY_PUBLIC = "public"
    VISIBILITY_CHOICES = [(VISIBILITY_PRIVATE, _("Private")), (VISIBILITY_PUBLIC, _("Public"))]

    user = models.ForeignKey(USER, on_delete=models.CASCADE, null=True, blank=True, related_name="wishlists")
    session_key = models.CharField(_("session key"), max_length=40, blank=True, db_index=True)
    name = models.CharField(_("name"), max_length=200, default="My Wishlist")
    is_default = models.BooleanField(_("default list"), default=False)
    visibility = models.CharField(
        _("visibility"), max_length=10, choices=VISIBILITY_CHOICES, default=VISIBILITY_PRIVATE
    )
    share_token = models.CharField(_("share token"), max_length=64, blank=True, null=True, unique=True)

    class Meta:
        verbose_name = _("wishlist")
        verbose_name_plural = _("wishlists")
        ordering = ["-is_default", "created_at"]
        indexes = [models.Index(fields=["user", "visibility"], name="eng_wishlist_user_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True),
                name="eng_one_default_wishlist",
            )
        ]

    def __str__(self):
        return f"Wishlist({self.user or self.session_key[:8]}): {self.name}"

    def generate_share_token(self):
        self.share_token = secrets.token_urlsafe(32)
        self.visibility = self.VISIBILITY_PUBLIC
        self.save(update_fields=["share_token", "visibility", "updated_at"])
        return self.share_token

    def revoke_share(self):
        self.share_token = None
        self.visibility = self.VISIBILITY_PRIVATE
        self.save(update_fields=["share_token", "visibility", "updated_at"])


class WishlistItem(ProductRefMixin, TimeStampedUUIDModel):
    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name="items")
    note = models.CharField(_("note"), max_length=255, blank=True)
    added_price = models.DecimalField(_("price when added"), max_digits=14, decimal_places=2, null=True, blank=True)
    price_alert_sent_at = models.DateTimeField(_("price-drop alert sent"), null=True, blank=True)

    class Meta:
        verbose_name = _("wishlist item")
        verbose_name_plural = _("wishlist items")
        ordering = ["-created_at"]
        unique_together = [("wishlist", "content_type", "object_id")]
        indexes = [models.Index(fields=["content_type", "object_id"], name="eng_wishitem_product_idx")]

    def __str__(self):
        return f"WishlistItem({self.object_id})"


# ── Reviews ───────────────────────────────────────────────────────────────────


class ProductReview(ProductRefMixin, TimeStampedUUIDModel):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField(_("rating"), validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(_("title"), max_length=200, blank=True)
    body = models.TextField(_("body"), max_length=5000)
    image_urls = models.JSONField(_("photos"), default=list, blank=True)
    is_verified_purchase = models.BooleanField(_("verified purchase"), default=False)
    is_approved = models.BooleanField(_("approved"), default=False, db_index=True)
    is_rejected = models.BooleanField(_("rejected"), default=False)
    moderation_note = models.CharField(_("moderation note"), max_length=255, blank=True)
    is_featured = models.BooleanField(_("featured"), default=False)
    helpful_count = models.PositiveIntegerField(_("helpful votes"), default=0)
    reply = models.TextField(_("seller / staff reply"), blank=True)
    replied_at = models.DateTimeField(_("replied at"), null=True, blank=True)

    class Meta:
        verbose_name = _("product review")
        verbose_name_plural = _("product reviews")
        ordering = ["-created_at"]
        unique_together = [("user", "content_type", "object_id")]
        indexes = [
            models.Index(fields=["content_type", "object_id", "is_approved"], name="eng_review_product_idx"),
            models.Index(fields=["rating", "is_approved"], name="eng_review_rating_idx"),
        ]

    def __str__(self):
        return f"Review({self.user} → {self.object_id}, rating={self.rating})"


class ReviewVote(TimeStampedUUIDModel):
    review = models.ForeignKey(ProductReview, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="review_votes")

    class Meta:
        verbose_name = _("review vote")
        verbose_name_plural = _("review votes")
        unique_together = [("review", "user")]


# ── Q&A ───────────────────────────────────────────────────────────────────────


class ProductQuestion(ProductRefMixin, TimeStampedUUIDModel):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="product_questions")
    question = models.TextField(_("question"), max_length=1000)
    is_approved = models.BooleanField(_("approved"), default=False, db_index=True)

    class Meta:
        verbose_name = _("product question")
        verbose_name_plural = _("product questions")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id", "is_approved"], name="eng_question_product_idx")]

    def __str__(self):
        return self.question[:60]


class ProductAnswer(TimeStampedUUIDModel):
    question = models.ForeignKey(ProductQuestion, on_delete=models.CASCADE, related_name="answers")
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="product_answers")
    answer = models.TextField(_("answer"), max_length=2000)
    is_official = models.BooleanField(_("official answer"), default=False, help_text=_("From staff or the seller."))
    is_approved = models.BooleanField(_("approved"), default=True)

    class Meta:
        verbose_name = _("product answer")
        verbose_name_plural = _("product answers")
        ordering = ["-is_official", "created_at"]

    def __str__(self):
        return self.answer[:60]


# ── Browsing & search history ─────────────────────────────────────────────────


class RecentlyViewed(ProductRefMixin, TimeStampedUUIDModel):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, null=True, blank=True, related_name="recently_viewed")
    session_key = models.CharField(_("session key"), max_length=40, blank=True, db_index=True)
    view_count = models.PositiveIntegerField(_("view count"), default=1)

    class Meta:
        verbose_name = _("recently viewed")
        verbose_name_plural = _("recently viewed items")
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "updated_at"], name="eng_rv_user_idx"),
            models.Index(fields=["session_key", "updated_at"], name="eng_rv_session_idx"),
        ]

    def __str__(self):
        return f"RecentlyViewed({self.user or self.session_key[:8]}: {self.object_id})"

    @classmethod
    def track(cls, request, product):
        """Convenience classmethod."""
        from .services import track_view

        return track_view(request, product)


class RecentSearch(TimeStampedUUIDModel):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, null=True, blank=True, related_name="recent_searches")
    session_key = models.CharField(_("session key"), max_length=40, blank=True, db_index=True)
    query = models.CharField(_("query"), max_length=255)

    class Meta:
        verbose_name = _("recent search")
        verbose_name_plural = _("recent searches")
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["user", "updated_at"], name="eng_rs_user_idx")]

    def __str__(self):
        return f"RecentSearch({self.query})"

    @classmethod
    def record(cls, request, query):
        """Convenience classmethod."""
        from .services import record_search

        return record_search(request, query)


class SearchTerm(TimeStampedUUIDModel):
    """Aggregated search counts for 'trending searches'."""

    term = models.CharField(_("term"), max_length=255, unique=True)
    count = models.PositiveIntegerField(_("searches"), default=0, db_index=True)
    is_hidden = models.BooleanField(_("hidden from trending"), default=False)

    class Meta:
        verbose_name = _("search term")
        verbose_name_plural = _("search terms")
        ordering = ["-count"]

    def __str__(self):
        return f"{self.term} ({self.count})"
