from rest_framework.routers import DefaultRouter

from .views import InventoryViewSet, StockAlertViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("inventory/alerts", StockAlertViewSet, basename="inventory-alert")
router.register("inventory", InventoryViewSet, basename="inventory")

urlpatterns = router.urls
