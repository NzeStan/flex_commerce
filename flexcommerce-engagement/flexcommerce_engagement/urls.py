from rest_framework.routers import DefaultRouter

from .views import (
    ProductQuestionViewSet,
    ProductReviewViewSet,
    RecentlyViewedViewSet,
    RecentSearchViewSet,
    WishlistViewSet,
)

router = DefaultRouter()
router.include_root_view = False
router.register("wishlists", WishlistViewSet, basename="wishlist")
router.register("reviews", ProductReviewViewSet, basename="review")
router.register("questions", ProductQuestionViewSet, basename="question")
router.register("recently-viewed", RecentlyViewedViewSet, basename="recently-viewed")
router.register("recent-searches", RecentSearchViewSet, basename="recent-search")

urlpatterns = router.urls
