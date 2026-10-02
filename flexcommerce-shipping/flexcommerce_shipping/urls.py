from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import PickupStationViewSet, ShippingMethodViewSet, ShippingZoneViewSet, StatesView

router = DefaultRouter()
router.include_root_view = False
router.register("shipping/zones", ShippingZoneViewSet, basename="shipping-zone")
router.register("shipping/methods", ShippingMethodViewSet, basename="shipping-method")
router.register("shipping/pickup-stations", PickupStationViewSet, basename="shipping-pickup-station")

urlpatterns = [path("shipping/states/", StatesView.as_view(), name="shipping-states")] + router.urls
