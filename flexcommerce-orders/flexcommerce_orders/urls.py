from rest_framework.routers import DefaultRouter

from .views import OrderViewSet, ReturnViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("orders", OrderViewSet, basename="order")
router.register("returns", ReturnViewSet, basename="return")

urlpatterns = router.urls
