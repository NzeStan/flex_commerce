from rest_framework.routers import DefaultRouter

from .views import CartViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("cart", CartViewSet, basename="cart")

urlpatterns = router.urls
