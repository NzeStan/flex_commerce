from rest_framework.routers import DefaultRouter

from .views import PayoutViewSet, VendorViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("vendors", VendorViewSet, basename="vendor")
router.register("payouts", PayoutViewSet, basename="payout")

urlpatterns = router.urls
