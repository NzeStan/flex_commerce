"""
Orders API.

Customers (own orders):
  GET  /orders/                      list (?status=)
  GET  /orders/{id}/                 detail with items, shipments, refunds, timeline
  POST /orders/{id}/cancel/          {reason}
  POST /orders/{id}/return/          {items: [{order_item_id, quantity}], reason_code, reason}
  GET  /orders/my-orders/            (alias of the list)
Guests:
  POST /orders/track/                {order_number, email}  -> order + access_token (throttled)
  GET  /orders/guest/{order_number}/?token=...
Staff:
  POST /orders/{id}/transition/      {to_state, note}
  POST /orders/{id}/mark-paid/       {amount?, reference?}   (bank transfer / cash on delivery)
  POST /orders/{id}/shipments/       {carrier, tracking_number, items?}
  POST /orders/{id}/shipments/{shipment_id}/events/   {status, description, location}
  POST /orders/{id}/deliver/
  POST /orders/{id}/refund/          {amount, reason, method, process?}
  POST /orders/{id}/refunds/{refund_id}/process/   (also: approve-refund/{refund_id}/)
  POST /orders/{id}/refunds/{refund_id}/reject/
  GET/POST /returns/ ... approve / reject / receive
"""

from django.db.models import Prefetch, Q
from django.http import Http404
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from flexcommerce_core.api import FlexCommerceAPIMixin, IsStaff, paginate

from .models import Order, OrderEvent, OrderItem, ReturnRequest, Shipment
from .serializers import (
    CancelSerializer,
    CreateRefundSerializer,
    CreateShipmentSerializer,
    GuestLookupSerializer,
    MarkPaidSerializer,
    OrderListSerializer,
    OrderSerializer,
    OrderTransitionSerializer,
    RefundSerializer,
    ReturnCreateSerializer,
    ReturnDecisionSerializer,
    ReturnRequestSerializer,
    ShipmentSerializer,
    ShipmentUpdateSerializer,
    StaffOrderSerializer,
)
from .services import OrderService, find_guest_order

UUID_RE = r"[0-9a-fA-F-]{32,36}"


def detail_queryset():
    return Order.objects.select_related("user").prefetch_related(
        Prefetch("items", queryset=OrderItem.objects.order_by("created_at")),
        Prefetch("shipments", queryset=Shipment.objects.prefetch_related("events")),
        "refunds",
        Prefetch("events", queryset=OrderEvent.objects.order_by("created_at")),
    )


def _items(data):
    return [{"order_item_id": str(i["order_item_id"]), "quantity": i["quantity"]} for i in data or []]


class OrderViewSet(FlexCommerceAPIMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if self.action in ("list", "my_orders"):
            qs = Order.objects.prefetch_related("items")
        else:
            qs = detail_queryset()
        if not user.is_staff or self.action == "my_orders":
            qs = qs.filter(user=user)
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if user.is_staff:
            if params.get("payment_status"):
                qs = qs.filter(payment_status=params["payment_status"])
            if params.get("search"):
                term = params["search"].strip()[:100]
                qs = qs.filter(Q(order_number__iexact=term) | Q(email__iexact=term) | Q(phone=term))
            if params.get("from_date"):
                qs = qs.filter(created_at__date__gte=params["from_date"])
            if params.get("to_date"):
                qs = qs.filter(created_at__date__lte=params["to_date"])
        return qs.order_by("-created_at")

    def get_serializer_class(self):
        if self.action in ("list", "my_orders"):
            return OrderListSerializer
        return StaffOrderSerializer if self.request.user.is_staff else OrderSerializer

    def _detail(self, order):
        order = detail_queryset().get(pk=order.pk)
        return Response(self.get_serializer_class()(order, context=self.get_serializer_context()).data)

    def _actor(self):
        return str(self.request.user)

    # customer actions
    @action(detail=False, methods=["get"], url_path="my-orders")
    def my_orders(self, request):
        return paginate(self, self.get_queryset(), OrderListSerializer)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        ser = CancelSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        OrderService(order).cancel(
            actor=self._actor(),
            reason=ser.validated_data.get("reason", ""),
            by_customer=not request.user.is_staff,
        )
        return self._detail(order)

    @action(detail=True, methods=["post"], url_path="return")
    def request_return(self, request, pk=None):
        order = self.get_object()
        ser = ReturnCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        rr = OrderService(order).request_return(
            _items(data["items"]),
            reason_code=data["reason_code"],
            reason=data.get("reason", ""),
            image_urls=data.get("image_urls"),
            refund_method=data["refund_method"],
            user=request.user,
            actor=self._actor(),
        )
        return Response(ReturnRequestSerializer(rr).data, status=status.HTTP_201_CREATED)

    # staff actions
    @action(detail=True, methods=["post"], permission_classes=[IsStaff])
    def transition(self, request, pk=None):
        order = self.get_object()
        ser = OrderTransitionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        OrderService(order).transition(
            ser.validated_data["to_state"],
            actor=self._actor(),
            note=ser.validated_data.get("note", ""),
        )
        return self._detail(order)

    @action(detail=True, methods=["post"], url_path="mark-paid", permission_classes=[IsStaff])
    def mark_paid(self, request, pk=None):
        order = self.get_object()
        ser = MarkPaidSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        OrderService(order).mark_paid(
            amount=ser.validated_data.get("amount"),
            reference=ser.validated_data.get("reference", ""),
            provider="manual",
            actor=self._actor(),
        )
        return self._detail(order)

    @action(detail=True, methods=["post"], url_path="shipments", permission_classes=[IsStaff])
    def create_shipment(self, request, pk=None):
        order = self.get_object()
        ser = CreateShipmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        shipment = OrderService(order).create_shipment(
            items=_items(data["items"]) if data.get("items") else None,
            carrier=data.get("carrier", ""),
            tracking_number=data.get("tracking_number", ""),
            tracking_url=data.get("tracking_url", ""),
            estimated_delivery=data.get("estimated_delivery"),
            actor=self._actor(),
        )
        return Response(ShipmentSerializer(shipment).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path=rf"shipments/(?P<shipment_id>{UUID_RE})/events",
        permission_classes=[IsStaff],
    )
    def shipment_event(self, request, pk=None, shipment_id=None):
        order = self.get_object()
        shipment = order.shipments.filter(pk=shipment_id).first()
        if shipment is None:
            raise Http404
        ser = ShipmentUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        OrderService(order).update_shipment(shipment, actor=self._actor(), **ser.validated_data)
        shipment.refresh_from_db()
        return Response(ShipmentSerializer(shipment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsStaff])
    def deliver(self, request, pk=None):
        order = self.get_object()
        OrderService(order).mark_delivered(actor=self._actor())
        return self._detail(order)

    @action(detail=True, methods=["post"], url_path="refund", permission_classes=[IsStaff])
    def create_refund(self, request, pk=None):
        order = self.get_object()
        ser = CreateRefundSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        service = OrderService(order)
        refund = service.create_refund(
            data["amount"],
            data["reason"],
            items=_items(data.get("items")),
            method=data["method"],
            actor=self._actor(),
        )
        if data["process"]:
            refund = service.process_refund(refund, actor=self._actor(), processed_by=request.user)
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)

    def _refund(self, order, refund_id):
        refund = order.refunds.filter(pk=refund_id).first()
        if refund is None:
            raise Http404
        return refund

    @action(
        detail=True,
        methods=["post"],
        url_path=rf"refunds/(?P<refund_id>{UUID_RE})/process",
        permission_classes=[IsStaff],
    )
    def process_refund(self, request, pk=None, refund_id=None):
        order = self.get_object()
        refund = OrderService(order).process_refund(
            self._refund(order, refund_id), actor=self._actor(), processed_by=request.user
        )
        return Response(RefundSerializer(refund).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=rf"approve-refund/(?P<refund_id>{UUID_RE})",
        permission_classes=[IsStaff],
    )
    def approve_refund(self, request, pk=None, refund_id=None):
        """Alias endpoint: processes the refund."""
        return self.process_refund(request, pk=pk, refund_id=refund_id)

    @action(
        detail=True,
        methods=["post"],
        url_path=rf"refunds/(?P<refund_id>{UUID_RE})/reject",
        permission_classes=[IsStaff],
    )
    def reject_refund(self, request, pk=None, refund_id=None):
        order = self.get_object()
        reason = str(request.data.get("reason", ""))[:500]
        refund = OrderService(order).reject_refund(self._refund(order, refund_id), reason=reason, actor=self._actor())
        return Response(RefundSerializer(refund).data)

    # guests
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        throttle_scope="guest_order_lookup",
    )
    def track(self, request):
        ser = GuestLookupSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        order = find_guest_order(ser.validated_data["order_number"], ser.validated_data["email"])
        if order is None:
            raise Http404
        data = OrderSerializer(detail_queryset().get(pk=order.pk)).data
        return Response({**data, "access_token": order.access_token})

    @action(
        detail=False,
        methods=["get"],
        url_path=r"guest/(?P<order_number>[A-Za-z0-9_-]+)",
        permission_classes=[permissions.AllowAny],
        throttle_scope="guest_order_lookup",
    )
    def guest_detail(self, request, order_number=None):
        import hmac

        token = request.query_params.get("token", "")
        order = Order.objects.filter(order_number=order_number).first()
        if order is None or not token or not hmac.compare_digest(order.access_token, token):
            raise Http404
        return Response(OrderSerializer(detail_queryset().get(pk=order.pk)).data)


class ReturnViewSet(FlexCommerceAPIMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Customers see their own returns; staff see and decide on all of them."""

    serializer_class = ReturnRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ReturnRequest.objects.select_related("order").order_by("-created_at")
        if not self.request.user.is_staff:
            qs = qs.filter(order__user=self.request.user)
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

    def _response(self, rr, refund=None):
        rr.refresh_from_db()
        payload = ReturnRequestSerializer(rr).data
        if refund is not None:
            payload["refund"] = RefundSerializer(refund).data
        return Response(payload)

    def _decision(self, request):
        ser = ReturnDecisionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        rr = self.get_object()
        return rr, OrderService(rr.order), ser.validated_data

    @action(detail=True, methods=["post"], permission_classes=[IsStaff])
    def approve(self, request, pk=None):
        rr, service, data = self._decision(request)
        service.approve_return(rr, actor=str(request.user), note=data.get("note", ""), approved_by=request.user)
        return self._response(rr)

    @action(detail=True, methods=["post"], permission_classes=[IsStaff])
    def reject(self, request, pk=None):
        rr, service, data = self._decision(request)
        service.reject_return(rr, actor=str(request.user), note=data.get("note", ""))
        return self._response(rr)

    @action(detail=True, methods=["post"], permission_classes=[IsStaff])
    def receive(self, request, pk=None):
        rr, service, data = self._decision(request)
        rr, refund = service.receive_return(
            rr,
            actor=str(request.user),
            restock=data["restock"],
            refund=data["refund"],
            note=data.get("note", ""),
        )
        return self._response(rr, refund)
