"""
Marketplace API.

Public:   GET /vendors/  GET /vendors/{slug}/   (approved shops)
Sellers:  POST /vendors/apply/
          GET|PATCH /vendors/me/        GET /vendors/me/summary/
          GET /vendors/me/orders/       POST /vendors/me/orders/{id}/ship/
          GET /vendors/me/payouts/
          (products: the catalog API — approved vendors manage their own products)
Staff:    GET /vendors/?status=pending, PATCH /vendors/{slug}/ (commission, official store)
          POST /vendors/{slug}/approve/ | reject/ | suspend/
          GET /payouts/  POST /payouts/generate/  POST /payouts/{id}/mark-paid/ | mark-failed/
"""

from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from flexcommerce_core.api import FlexCommerceAPIMixin, IsStaff, paginate

from . import services
from .models import Payout, Vendor, VendorOrder
from .serializers import (
    PayoutDecisionSerializer,
    PayoutSerializer,
    PublicVendorSerializer,
    ShipSerializer,
    StaffVendorSerializer,
    StatusReasonSerializer,
    VendorApplySerializer,
    VendorOrderSerializer,
    VendorProfileSerializer,
)


class VendorViewSet(
    FlexCommerceAPIMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    lookup_field = "slug"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.AllowAny()]
        if self.action in ("partial_update", "update", "approve", "reject", "suspend"):
            return [IsStaff()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        qs = Vendor.objects.all()
        if self.request.user.is_staff:
            if self.request.query_params.get("status"):
                qs = qs.filter(status=self.request.query_params["status"])
            return qs
        return qs.filter(status=Vendor.STATUS_APPROVED)

    def get_serializer_class(self):
        return StaffVendorSerializer if self.request.user.is_staff else PublicVendorSerializer

    @action(detail=False, methods=["post"], throttle_scope="vendor_signup")
    def apply(self, request):
        ser = VendorApplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        vendor = services.apply(request.user, **ser.validated_data)
        return Response(VendorProfileSerializer(vendor).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get", "patch"])
    def me(self, request):
        vendor = services.require_vendor(request.user, approved=False)
        if request.method == "PATCH":
            ser = VendorProfileSerializer(vendor, data=request.data, partial=True)
            ser.is_valid(raise_exception=True)
            ser.save()
        return Response(VendorProfileSerializer(vendor).data)

    @action(detail=False, methods=["get"], url_path="me/summary")
    def summary(self, request):
        vendor = services.require_vendor(request.user)
        return Response(
            {k: (str(v) if hasattr(v, "quantize") else v) for k, v in services.vendor_summary(vendor).items()}
        )

    @action(detail=False, methods=["get"], url_path="me/orders")
    def orders(self, request):
        vendor = services.require_vendor(request.user)
        qs = VendorOrder.objects.filter(vendor=vendor).select_related("order").prefetch_related("order__items")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return paginate(self, qs.order_by("-created_at"), VendorOrderSerializer)

    @action(detail=False, methods=["post"], url_path=r"me/orders/(?P<vendor_order_id>[0-9a-f-]+)/ship")
    def ship(self, request, vendor_order_id=None):
        vendor = services.require_vendor(request.user)
        vendor_order = services.get_vendor_order(vendor, vendor_order_id)
        ser = ShipSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.ship_vendor_order(vendor_order, actor=str(request.user), **ser.validated_data)
        vendor_order.refresh_from_db()
        return Response(VendorOrderSerializer(vendor_order).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="me/payouts")
    def my_payouts(self, request):
        vendor = services.require_vendor(request.user)
        return paginate(self, Payout.objects.filter(vendor=vendor), PayoutSerializer)

    def _status(self, request, status_value):
        vendor = self.get_object()
        ser = StatusReasonSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.set_status(vendor, status_value, ser.validated_data.get("reason", ""))
        return Response(StaffVendorSerializer(vendor).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, slug=None):
        return self._status(request, Vendor.STATUS_APPROVED)

    @action(detail=True, methods=["post"])
    def reject(self, request, slug=None):
        return self._status(request, Vendor.STATUS_REJECTED)

    @action(detail=True, methods=["post"])
    def suspend(self, request, slug=None):
        return self._status(request, Vendor.STATUS_SUSPENDED)


class PayoutViewSet(FlexCommerceAPIMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = PayoutSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = Payout.objects.select_related("vendor")
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

    @action(detail=False, methods=["post"])
    def generate(self, request):
        return Response(services.generate_payouts())

    def _decide(self, request, paid):
        ser = PayoutDecisionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payout = services.mark_payout(
            self.get_object(),
            paid,
            reference=ser.validated_data.get("reference", ""),
            reason=ser.validated_data.get("reason", ""),
        )
        return Response(PayoutSerializer(payout).data)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        return self._decide(request, True)

    @action(detail=True, methods=["post"], url_path="mark-failed")
    def mark_failed(self, request, pk=None):
        return self._decide(request, False)
