"""
Checkout API.

  POST /checkout/                       place the order (see CheckoutSerializer)
  POST /checkout/preview/               totals for an address + delivery method (no side effects)
  POST /checkout/shipping-options/      delivery options for the current cart and an address
  GET  /checkout/payment-methods/       available payment methods
"""

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from flexcommerce_cart.services import CartSessionManager
from flexcommerce_core.api import FlexCommerceAPIMixin
from flexcommerce_core.exceptions import EmptyCartCheckoutError
from flexcommerce_core.utils.products import is_installed
from flexcommerce_core.utils.vat import ZERO

from .serializers import CheckoutSerializer, PreviewSerializer, ShippingOptionsSerializer
from .services import CheckoutService, available_payment_methods, replay_for_request


def _cart(request):
    cart = CartSessionManager.get_cart(request, create=False)
    if cart is None or not cart.items.exists():
        raise EmptyCartCheckoutError()
    return cart


class CheckoutView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "checkout"

    def post(self, request):
        ser = CheckoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if data.get("idempotency_key"):
            replay = replay_for_request(request, data["idempotency_key"])
            if replay is not None:
                return Response(replay.to_dict({"request": request}), status=status.HTTP_200_OK)
        cart = _cart(request)
        result = CheckoutService(cart, request).execute(
            payment_method=data["payment_method"],
            shipping_address=data.get("shipping_address"),
            shipping_address_id=data.get("shipping_address_id"),
            billing_address=data.get("billing_address"),
            shipping_method_id=data.get("shipping_method_id"),
            pickup_station_id=data.get("pickup_station_id"),
            customer_note=data.get("customer_note", ""),
            idempotency_key=data.get("idempotency_key") or None,
            email=data.get("email", ""),
            phone=data.get("phone", ""),
            callback_url=data.get("callback_url", ""),
            expected_total=data.get("expected_total"),
            save_address=data.get("save_address"),
        )
        code = status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED
        return Response(result.to_dict({"request": request}), status=code)


class PreviewView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = PreviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        totals = CheckoutService(_cart(request), request).preview(
            shipping_address=data.get("shipping_address"),
            shipping_address_id=data.get("shipping_address_id"),
            shipping_method_id=data.get("shipping_method_id"),
            pickup_station_id=data.get("pickup_station_id"),
        )
        return Response({k: (str(v) if hasattr(v, "quantize") else v) for k, v in totals.items()})


class ShippingOptionsView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = ShippingOptionsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if not is_installed("flexcommerce_shipping"):
            return Response({"deliverable": True, "zone": None, "options": []})
        from flexcommerce_cart.services import CartPricingService
        from flexcommerce_shipping.services import quote_all

        cart = _cart(request)
        state, country = ser.validated_data.get("state", ""), ser.validated_data.get("country", "")
        if ser.validated_data.get("shipping_address_id") and cart.user_id:
            from flexcommerce_core.models import Address

            address = Address.objects.filter(pk=ser.validated_data["shipping_address_id"], user=cart.user).first()
            if address is not None:
                state, country = address.state, address.country
        lines, _ = CartPricingService(cart).priced_lines()
        zone, quotes = quote_all(
            state=state,
            country=country,
            cart_total=cart.total_amount,
            item_count=sum(line.quantity for line in lines),
            weight=sum((line.weight * line.quantity for line in lines), ZERO),
            free_shipping=cart.free_shipping,
        )
        return Response(
            {
                "deliverable": zone is not None,
                "zone": zone.zone_code if zone else None,
                "options": [q.to_dict() for q in quotes],
            }
        )


class PaymentMethodsView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        user = request.user if request.user.is_authenticated else None
        return Response(available_payment_methods(user=user))
