from rest_framework.routers import DefaultRouter

from .views import CouponViewSet, FlashSaleViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("coupons", CouponViewSet, basename="coupon")
router.register("flash-sales", FlashSaleViewSet, basename="flash-sale")

urlpatterns = router.urls
