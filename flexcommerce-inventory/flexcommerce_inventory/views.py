"""
Inventory API.

Staff:
  GET  /inventory/                     list (?low_stock=1, ?search=sku)
  POST /inventory/                     start tracking a product
  POST /inventory/{id}/restock/        {quantity, note, reference}
  POST /inventory/{id}/adjust/         {on_hand, note}   (stock take)
  GET  /inventory/{id}/movements/
  GET  /inventory/reservations/        (?status=pending&order_ref=...)
Anyone:
  POST   /inventory/alerts/            back-in-stock subscription (email if anonymous)
  GET    /inventory/alerts/            the signed-in user's subscriptions
  DELETE /inventory/alerts/{id}/
"""

from django.contrib.contenttypes.models import ContentType
from django.db.models import F, IntegerField, Q
from django.db.models.functions import Cast
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from flexcommerce_core.api import FlexCommerceAPIMixin, IsStaff, paginate
from flexcommerce_core.conf import fc_setting
from flexcommerce_core.utils.products import get_product

from . import services
from .models import InventoryItem, StockAlert, StockReservation
from .serializers import (
    AdjustSerializer,
    InventoryCreateSerializer,
    InventoryItemSerializer,
    QuantitySerializer,
    StockAlertSerializer,
    StockMovementSerializer,
    StockReservationSerializer,
)


class InventoryViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    serializer_class = InventoryItemSerializer
    permission_classes = [IsStaff]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = InventoryItem.objects.select_related("content_type").order_by("sku", "created_at")
        params = self.request.query_params
        if params.get("low_stock") in ("1", "true"):
            threshold = int(fc_setting("LOW_STOCK_THRESHOLD", 5))
            available = Cast(F("on_hand"), IntegerField()) - Cast(F("reserved"), IntegerField())
            qs = qs.annotate(avail=available).filter(
                Q(reorder_point__isnull=False, avail__lte=F("reorder_point"))
                | Q(reorder_point__isnull=True, avail__lte=threshold)
            )
        if params.get("search"):
            qs = qs.filter(sku__icontains=params["search"][:100])
        return qs

    def create(self, request, *args, **kwargs):
        ser = InventoryCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        product = get_product(data.get("product_type"), data["product_id"], for_purchase=False)
        ct = ContentType.objects.get_for_model(product)
        if InventoryItem.objects.filter(content_type=ct, object_id=product.pk).exists():
            raise serializers.ValidationError({"product_id": "This product is already tracked."})
        item = InventoryItem.objects.create(
            content_type=ct,
            object_id=product.pk,
            sku=data.get("sku") or str(getattr(product, "sku", "") or "")[:100],
            reorder_point=data.get("reorder_point"),
            allow_oversell=data["allow_oversell"],
        )
        if data["on_hand"]:
            item.restock(data["on_hand"], note="Initial stock")
        return Response(InventoryItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def restock(self, request, pk=None):
        item = self.get_object()
        ser = QuantitySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        item.restock(
            ser.validated_data["quantity"],
            note=ser.validated_data.get("note", ""),
            reference=ser.validated_data.get("reference", ""),
        )
        return Response(InventoryItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        item = self.get_object()
        ser = AdjustSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        item.adjust(ser.validated_data["on_hand"], note=ser.validated_data.get("note", ""))
        return Response(InventoryItemSerializer(item).data)

    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None):
        item = self.get_object()
        return paginate(self, item.movements.order_by("-created_at"), StockMovementSerializer)

    @action(detail=False, methods=["get"])
    def reservations(self, request):
        qs = StockReservation.objects.order_by("-created_at")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        if request.query_params.get("order_ref"):
            qs = qs.filter(order_ref=request.query_params["order_ref"])
        return paginate(self, qs, StockReservationSerializer)


class StockAlertViewSet(
    FlexCommerceAPIMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = StockAlertSerializer
    throttle_scope = "stock_alert"

    def get_permissions(self):
        if self.action == "create":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        return StockAlert.objects.filter(user=self.request.user).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        user = request.user if request.user.is_authenticated else None
        if user is None and not data.get("email"):
            raise serializers.ValidationError({"email": "Email is required when not signed in."})
        product = get_product(data.get("product_type"), data["object_id"], for_purchase=False)
        alert = services.subscribe_alert(product, user=user, email=data.get("email", ""), phone=data.get("phone", ""))
        return Response(StockAlertSerializer(alert).data, status=status.HTTP_201_CREATED)
