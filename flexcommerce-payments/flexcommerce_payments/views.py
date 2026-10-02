"""
Payments API.

  GET  /payments/methods/?order_id=         available methods for an order (or in general)
  POST /payments/initiate/                  {provider, order_id | order_number+token, callback_url?}
  GET  /payments/verify/?reference=         re-check with the provider (call after the redirect)
  POST /payments/webhooks/{provider}/       provider → us (signature-verified, CSRF-exempt)
  GET  /payments/                           own payments (staff: all)
  GET  /wallet/                             balance
  GET  /wallet/transactions/
  POST /wallet/topup/                       {amount, provider, callback_url?}
  POST /wallet/adjust/                      staff: {user_id, amount (+/-), description}
"""

import hmac
import secrets

from django.contrib.auth import get_user_model
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from flexcommerce_core.api import FlexCommerceAPIMixin, IsStaff, paginate
from flexcommerce_core.exceptions import FlexCommerceError, WalletError

from .gateways import available_gateways
from .models import Payment
from .serializers import (
    InitiatePaymentSerializer,
    PaymentSerializer,
    TopUpSerializer,
    WalletAdjustSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from .services import PaymentService, WalletService


def _order_for(request, data):
    """Resolve the order the caller may pay for (owner, staff, or guest with token)."""
    from flexcommerce_orders.models import Order

    qs = Order.objects.select_related("user")
    order = (
        qs.filter(pk=data["order_id"]).first()
        if data.get("order_id")
        else qs.filter(order_number=data.get("order_number", "")).first()
    )
    if order is None:
        raise Http404
    user = request.user
    if user.is_authenticated and (user.is_staff or order.user_id == user.pk):
        return order
    token = data.get("token") or ""
    if order.user_id is None and token and hmac.compare_digest(order.access_token, token):
        return order
    raise Http404


class PaymentMethodsView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        order = None
        if request.query_params.get("order_id") or request.query_params.get("order_number"):
            order = _order_for(request, request.query_params)
        user = request.user if request.user.is_authenticated else None
        return Response([g.describe(order) for g in available_gateways(order=order, user=user)])


class InitiatePaymentView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "payment"

    def post(self, request):
        ser = InitiatePaymentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        order = _order_for(request, data)
        payment = PaymentService.start(
            order, data["provider"], callback_url=data.get("callback_url", ""), user=request.user
        )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class VerifyPaymentView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "payment"

    def get(self, request):
        reference = (
            request.query_params.get("reference")
            or request.query_params.get("trxref")
            or request.query_params.get("tx_ref")
            or ""
        )
        if not reference:
            return Response(
                {"error": "reference_required", "detail": "reference is required.", "extra": {}}, status=400
            )
        payment = PaymentService.verify(reference[:100])
        return Response(PaymentSerializer(payment).data)

    post = get


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(APIView):
    """Gateways call this server-to-server. Always 200 for valid signatures (providers retry otherwise)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, provider):
        try:
            log = PaymentService.handle_webhook(provider, request.body, request.headers)
        except KeyError:
            raise Http404 from None
        except FlexCommerceError as exc:
            return Response({"error": exc.code}, status=exc.status_code)
        return Response({"received": True, "processed": log.processed})


class PaymentViewSet(FlexCommerceAPIMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Payment.objects.select_related("order").order_by("-created_at")
        if self.request.user.is_staff:
            if self.request.query_params.get("status"):
                qs = qs.filter(status=self.request.query_params["status"])
            return qs
        return qs.filter(user=self.request.user)


class WalletViewSet(FlexCommerceAPIMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WalletSerializer

    def list(self, request):
        return Response(WalletSerializer(WalletService.get_wallet(request.user)).data)

    @action(detail=False, methods=["get"])
    def transactions(self, request):
        wallet = WalletService.get_wallet(request.user)
        return paginate(self, wallet.transactions.select_related("order"), WalletTransactionSerializer)

    @action(detail=False, methods=["post"], throttle_scope="payment")
    def topup(self, request):
        ser = TopUpSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payment = WalletService.start_topup(
            request.user,
            ser.validated_data["amount"],
            ser.validated_data["provider"],
            ser.validated_data.get("callback_url", ""),
        )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], permission_classes=[IsStaff])
    def adjust(self, request):
        ser = WalletAdjustSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        user = get_user_model().objects.filter(pk=data["user_id"]).first()
        if user is None:
            raise Http404
        reference = data.get("reference") or f"adjust:{secrets.token_hex(8)}"
        amount = data["amount"]
        if amount == 0:
            raise WalletError("Amount must not be zero.")
        if amount > 0:
            txn = WalletService.credit(user, amount, reference, source="adjustment", description=data["description"])
        else:
            txn = WalletService.debit(user, -amount, reference, source="adjustment", description=data["description"])
        return Response(WalletTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)
