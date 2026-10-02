"""
Engagement API.

Wishlists (signed in):  /wishlists/ CRUD, POST {id}/add/, DELETE {id}/remove/{item}/,
  POST|DELETE {id}/share/, GET shared/{token}/ (public), POST toggle/, GET contains/?product_ids=,
  POST {id}/items/{item}/move-to-cart/
Reviews:  GET /reviews/?product_type=&product_id=&rating=&verified=1&ordering=helpful|newest|highest|lowest
  POST /reviews/, PATCH/DELETE own, POST {id}/helpful/, GET summary/, GET mine/,
  staff: POST {id}/approve/ {id}/reject/; staff or the product's vendor: POST {id}/reply/
Questions: GET/POST /questions/, POST {id}/answers/, staff POST {id}/approve/
History:  GET/POST /recently-viewed/ (+ track/, clear/), GET/POST /recent-searches/ (+ clear/, trending/)
"""

from django.db.models import Prefetch
from django.http import Http404
from django.utils import timezone
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from flexcommerce_core import hooks
from flexcommerce_core.api import FlexCommerceAPIMixin, IsStaff
from flexcommerce_core.events import emit
from flexcommerce_core.exceptions import FlexCommerceError, PermissionDeniedError
from flexcommerce_core.utils.products import is_installed

from . import services, signals
from .models import (
    ProductAnswer,
    ProductQuestion,
    ProductReview,
    RecentlyViewed,
    RecentSearch,
    ReviewVote,
    SearchTerm,
    Wishlist,
    WishlistItem,
)
from .serializers import (
    AddToWishlistSerializer,
    ModerationSerializer,
    ProductAnswerSerializer,
    ProductQuestionSerializer,
    ProductRefSerializer,
    ProductReviewSerializer,
    QuestionCreateSerializer,
    RecentlyViewedSerializer,
    RecentSearchSerializer,
    ReplySerializer,
    ReviewCreateSerializer,
    SearchQuerySerializer,
    SearchTermSerializer,
    WishlistItemSerializer,
    WishlistSerializer,
)


def _product_filter(request):
    """``(content_type, object_id)`` from ``?product_type=&product_id=`` or ``None``."""
    product_id = request.query_params.get("product_id")
    if not product_id:
        return None
    product = services.resolve_product(request.query_params.get("product_type"), product_id)
    return services.product_ref(product)


def _is_vendor_of(user, product):
    vendor_id = getattr(product, "vendor_id", None)
    if not vendor_id or not user.is_authenticated:
        return False
    return any(v and str(v) == str(vendor_id) for v in hooks.run("catalog.vendor_id_for_user", user))


# ── Wishlists ────────────────────────────────────────────────────────────────


class WishlistViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    serializer_class = WishlistSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return Wishlist.objects.filter(user=self.request.user).prefetch_related(
            Prefetch("items", queryset=WishlistItem.objects.select_related("content_type"))
        )

    def list(self, request, *args, **kwargs):
        services.default_wishlist(request.user)
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        if instance.is_default:
            raise FlexCommerceError("Your default wishlist cannot be deleted.", code="default_wishlist")
        instance.delete()

    @action(detail=True, methods=["post"])
    def add(self, request, pk=None):
        wishlist = self.get_object()
        ser = AddToWishlistSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        product = services.resolve_product(ser.validated_data["product_type"], ser.validated_data["product_id"])
        item, created = services.add_to_wishlist(wishlist, product, note=ser.validated_data.get("note", ""))
        return Response(WishlistItemSerializer(item).data, status=201 if created else 200)

    @action(detail=True, methods=["delete"], url_path=r"remove/(?P<item_id>[0-9a-f-]+)")
    def remove_item(self, request, pk=None, item_id=None):
        WishlistItem.objects.filter(wishlist=self.get_object(), pk=item_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post", "delete"])
    def share(self, request, pk=None):
        wishlist = self.get_object()
        if request.method == "DELETE":
            wishlist.revoke_share()
            return Response(status=status.HTTP_204_NO_CONTENT)
        token = wishlist.generate_share_token()
        return Response({"share_token": token, "share_path": f"wishlists/shared/{token}/"})

    @action(
        detail=False,
        methods=["get"],
        url_path=r"shared/(?P<token>[A-Za-z0-9_-]+)",
        permission_classes=[permissions.AllowAny],
    )
    def shared(self, request, token=None):
        wishlist = Wishlist.objects.filter(share_token=token, visibility=Wishlist.VISIBILITY_PUBLIC).first()
        if wishlist is None:
            raise Http404
        data = WishlistSerializer(wishlist).data
        data.pop("share_token", None)
        return Response(data)

    @action(detail=False, methods=["post"])
    def toggle(self, request):
        ser = ProductRefSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        product = services.resolve_product(ser.validated_data["product_type"], ser.validated_data["product_id"])
        item = services.toggle_wishlist(request.user, product)
        return Response({"wishlisted": item is not None, "item_id": str(item.pk) if item else None})

    @action(detail=False, methods=["get"])
    def contains(self, request):
        ids = [i for i in request.query_params.get("product_ids", "").split(",") if i][:200]
        found = WishlistItem.objects.filter(wishlist__user=request.user, object_id__in=_valid_uuids(ids))
        return Response({"product_ids": sorted({str(pk) for pk in found.values_list("object_id", flat=True)})})

    @action(detail=True, methods=["post"], url_path=r"items/(?P<item_id>[0-9a-f-]+)/move-to-cart")
    def move_to_cart(self, request, pk=None, item_id=None):
        if not is_installed("flexcommerce_cart"):
            raise FlexCommerceError("The cart is not enabled.", code="cart_not_installed")
        from flexcommerce_cart.services import CartService, CartSessionManager

        item = WishlistItem.objects.filter(wishlist=self.get_object(), pk=item_id).first()
        if item is None or item.product is None:
            raise Http404
        purchasable = services.purchasable_for(item.product)
        if purchasable is None:
            raise FlexCommerceError("This product cannot be added to the cart.", code="product_unavailable")
        cart = CartSessionManager.get_or_create_cart(request)
        CartService(cart).add_item(purchasable, 1)
        item.delete()
        return Response({"moved": True, "cart_id": str(cart.pk)})


def _valid_uuids(values):
    import uuid

    result = []
    for value in values:
        try:
            result.append(uuid.UUID(value))
        except ValueError:
            continue
    return result


# ── Reviews ──────────────────────────────────────────────────────────────────


class IsOwnerForWrite(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_staff or obj.user_id == request.user.pk


class ProductReviewViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    serializer_class = ProductReviewSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_throttles(self):
        self.throttle_scope = "review" if self.action in ("create", "helpful") else None
        return super().get_throttles()

    def get_permissions(self):
        if self.action in ("list", "retrieve", "summary"):
            return [permissions.AllowAny()]
        if self.action in ("approve", "reject"):
            return [IsStaff()]
        if self.action in ("helpful", "reply", "mine", "create"):
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsOwnerForWrite()]

    def get_queryset(self):
        qs = ProductReview.objects.select_related("user", "content_type")
        user = self.request.user
        if self.action in ("list", "retrieve", "helpful"):
            status_filter = self.request.query_params.get("status")
            if user.is_staff and status_filter == "pending":
                qs = qs.filter(is_approved=False, is_rejected=False)
            elif not user.is_staff or not status_filter:
                qs = qs.filter(is_approved=True)
        elif not user.is_staff and self.action not in ("reply",):
            qs = qs.filter(user=user)
        ref = _product_filter(self.request) if self.action == "list" else None
        if ref:
            qs = qs.filter(content_type=ref[0], object_id=ref[1])
        params = self.request.query_params
        if params.get("rating"):
            qs = qs.filter(rating=params["rating"]) if params["rating"].isdigit() else qs.none()
        if params.get("verified") in ("1", "true"):
            qs = qs.filter(is_verified_purchase=True)
        ordering = {
            "helpful": ["-is_featured", "-helpful_count", "-created_at"],
            "highest": ["-rating", "-created_at"],
            "lowest": ["rating", "-created_at"],
        }.get(params.get("ordering"), ["-is_featured", "-created_at"])
        return qs.order_by(*ordering)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.request.user.is_authenticated:
            context["voted_ids"] = set(
                ReviewVote.objects.filter(user=self.request.user).values_list("review_id", flat=True)
            )
        return context

    def create(self, request, *args, **kwargs):
        ser = ReviewCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        product = services.resolve_product(data["product_type"], data["product_id"])
        review = services.create_review(
            request.user,
            product,
            data["rating"],
            data["body"],
            title=data.get("title", ""),
            image_urls=data.get("image_urls"),
        )
        return Response(ProductReviewSerializer(review, context=self.get_serializer_context()).data, status=201)

    def perform_update(self, serializer):
        services.update_review(serializer.instance, **serializer.validated_data)

    def perform_destroy(self, instance):
        services.delete_review(instance)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        ref = _product_filter(request)
        if ref is None:
            raise FlexCommerceError("product_id is required.", code="product_required")
        summary = services.rating_summary(*ref)
        return Response({**summary, "average": str(summary["average"])})

    @action(detail=False, methods=["get"])
    def mine(self, request):
        qs = ProductReview.objects.filter(user=request.user).order_by("-created_at")
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    @action(detail=True, methods=["post"])
    def helpful(self, request, pk=None):
        review = self.get_object()
        voted = services.toggle_helpful(review, request.user)
        review.refresh_from_db()
        return Response({"voted": voted, "helpful_count": review.helpful_count})

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        ser = ModerationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        review = services.moderate_review(self._any_review(pk), True, ser.validated_data.get("note", ""))
        return Response(ProductReviewSerializer(review).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        ser = ModerationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        review = services.moderate_review(self._any_review(pk), False, ser.validated_data.get("note", ""))
        return Response(ProductReviewSerializer(review).data)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        review = self._any_review(pk)
        if not (request.user.is_staff or _is_vendor_of(request.user, review.product)):
            raise PermissionDeniedError("Only staff or the seller can reply.")
        ser = ReplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        review.reply = ser.validated_data["reply"]
        review.replied_at = timezone.now()
        review.save(update_fields=["reply", "replied_at", "updated_at"])
        return Response(ProductReviewSerializer(review).data)

    @staticmethod
    def _any_review(pk):
        review = ProductReview.objects.filter(pk=pk).first()
        if review is None:
            raise Http404
        return review


# ── Q&A ──────────────────────────────────────────────────────────────────────


class ProductQuestionViewSet(
    FlexCommerceAPIMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ProductQuestionSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.AllowAny()]
        if self.action == "approve":
            return [IsStaff()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        qs = ProductQuestion.objects.prefetch_related("answers")
        user = self.request.user
        if self.action == "destroy" and not user.is_staff:
            return qs.filter(user=user)
        if not (user.is_staff and self.request.query_params.get("status") == "pending"):
            qs = qs.filter(is_approved=True)
        else:
            qs = qs.filter(is_approved=False)
        ref = _product_filter(self.request) if self.action == "list" else None
        if ref:
            qs = qs.filter(content_type=ref[0], object_id=ref[1])
        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        from .conf import engagement_setting

        ser = QuestionCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        product = services.resolve_product(ser.validated_data["product_type"], ser.validated_data["product_id"])
        ct, pk = services.product_ref(product)
        question = ProductQuestion.objects.create(
            content_type=ct,
            object_id=pk,
            user=request.user,
            question=ser.validated_data["question"],
            is_approved=not engagement_setting("QUESTIONS_REQUIRE_APPROVAL"),
        )
        emit(
            "question.asked",
            signal=signals.question_asked,
            sender=ProductQuestion,
            question=question,
        )
        return Response(ProductQuestionSerializer(question).data, status=201)

    @action(detail=True, methods=["post"])
    def answers(self, request, pk=None):
        question = ProductQuestion.objects.filter(pk=pk, is_approved=True).first()
        if question is None:
            raise Http404
        ser = ProductAnswerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        official = request.user.is_staff or _is_vendor_of(request.user, question.product)
        answer = ProductAnswer.objects.create(
            question=question,
            user=request.user,
            is_official=official,
            answer=ser.validated_data["answer"],
        )
        emit(
            "question.answered",
            signal=signals.question_answered,
            sender=ProductQuestion,
            question=question,
            answer=answer,
        )
        return Response(ProductAnswerSerializer(answer).data, status=201)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        updated = ProductQuestion.objects.filter(pk=pk).update(is_approved=True)
        if not updated:
            raise Http404
        return Response({"approved": True})


# ── History ──────────────────────────────────────────────────────────────────


def _history_owner(request):
    owner = services._owner(request)
    return owner or {"pk__in": []}


class RecentlyViewedViewSet(FlexCommerceAPIMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = RecentlyViewedSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        return (
            RecentlyViewed.objects.filter(**_history_owner(self.request))
            .select_related("content_type")
            .order_by("-updated_at")
        )

    @action(detail=False, methods=["post"])
    def track(self, request):
        ser = ProductRefSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        product = services.resolve_product(ser.validated_data["product_type"], ser.validated_data["product_id"])
        services.track_view(request, product)
        return Response({"tracked": True}, status=201)

    @action(detail=False, methods=["delete"])
    def clear(self, request):
        RecentlyViewed.objects.filter(**_history_owner(request)).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecentSearchViewSet(FlexCommerceAPIMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = RecentSearchSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        return RecentSearch.objects.filter(**_history_owner(self.request)).order_by("-updated_at")

    def create(self, request):
        ser = SearchQuerySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response({"query": services.record_search(request, ser.validated_data["query"])}, status=201)

    @action(detail=False, methods=["delete"])
    def clear(self, request):
        RecentSearch.objects.filter(**_history_owner(request)).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"])
    def trending(self, request):
        terms = SearchTerm.objects.filter(is_hidden=False).order_by("-count")[:20]
        return Response(SearchTermSerializer(terms, many=True).data)
