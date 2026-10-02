"""
Shipping API.

Public:
  POST /shipping/methods/calculate/   {state, country?, cart_total, item_count, weight}
  GET  /shipping/states/              Nigerian states with their zones
  GET  /shipping/pickup-stations/     ?state=Lagos&city=Ikeja
Staff: CRUD for /shipping/zones/, /shipping/methods/, /shipping/pickup-stations/
"""

from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from flexcommerce_core.api import FlexCommerceAPIMixin, IsStaff, ReadOnlyOrStaff

from . import services
from .models import PickupStation, ShippingMethod, ShippingZone, normalise_region
from .serializers import (
    PickupStationSerializer,
    ShippingCostSerializer,
    ShippingMethodSerializer,
    ShippingZoneSerializer,
)


class ShippingZoneViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    queryset = ShippingZone.objects.all()
    serializer_class = ShippingZoneSerializer
    permission_classes = [IsStaff]
    pagination_class = None


class ShippingMethodViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    queryset = ShippingMethod.objects.prefetch_related("zones")
    serializer_class = ShippingMethodSerializer
    permission_classes = [IsStaff]
    pagination_class = None

    @action(detail=False, methods=["post"], permission_classes=[permissions.AllowAny])
    def calculate(self, request):
        """Shipping options and costs for an address."""
        ser = ShippingCostSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        zone, quotes = services.quote_all(
            state=data.get("state", ""),
            country=data.get("country", ""),
            cart_total=data["cart_total"],
            item_count=data["item_count"],
            weight=data["weight"],
        )
        return Response(
            {
                "zone": zone.zone_code if zone else None,
                "deliverable": zone is not None,
                "options": [q.to_dict() for q in quotes],
            }
        )


class PickupStationViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    serializer_class = PickupStationSerializer
    permission_classes = [ReadOnlyOrStaff]

    def get_queryset(self):
        qs = PickupStation.objects.select_related("zone")
        if not self.request.user.is_staff:
            qs = qs.filter(is_active=True)
        state = self.request.query_params.get("state")
        if state:
            qs = qs.filter(state__iexact=normalise_region(state))
        city = self.request.query_params.get("city")
        if city:
            qs = qs.filter(city__iexact=city.strip())
        return qs


class StatesView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(services.nigerian_states())
