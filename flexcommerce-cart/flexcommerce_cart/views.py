"""
Cart API.

  GET    /cart/                      current cart (never creates a row)
  POST   /cart/add/                  {product_id, product_type?, quantity}
  PATCH  /cart/{item_id}/update/     {quantity}   (0 removes)
  DELETE /cart/{item_id}/remove/
  POST   /cart/clear/
  POST   /cart/coupon/               {code}
  DELETE /cart/coupon/remove/
  POST   /cart/contact/              {email, phone}   guest contact (cart recovery)
  POST   /cart/{item_id}/save/       save for later (signed in)
  GET    /cart/saved/
  POST   /cart/{saved_id}/restore/
  DELETE /cart/saved/{saved_id}/

Anonymous clients get a ``cart_token`` in every response and the
``X-Cart-Token`` response header; send it back as a request header.
"""

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from flexcommerce_core.api import FlexCommerceAPIMixin
from flexcommerce_core.exceptions import CartItemNotFoundError

from .conf import cart_setting
from .models import SavedItem
from .serializers import (
    EMPTY_CART,
    AddItemSerializer,
    ApplyCouponSerializer,
    CartSerializer,
    ContactSerializer,
    SavedItemSerializer,
    UpdateQuantitySerializer,
    display_map,
)
from .services import CartService, CartSessionManager


class CartViewSet(FlexCommerceAPIMixin, GenericViewSet):
    permission_classes = [AllowAny]
    serializer_class = CartSerializer
    pagination_class = None

    def finalize_response(self, request, response, *args, **kwargs):
        token = getattr(request, "flexcommerce_cart_token", None)
        if token:
            response[cart_setting("CART_TOKEN_HEADER")] = token
        return super().finalize_response(request, response, *args, **kwargs)

    def _cart(self, request, create=True):
        return CartSessionManager.get_cart(request, create=create)

    def _render(self, cart, status_code=status.HTTP_200_OK):
        if cart is None:
            return Response(EMPTY_CART, status=status_code)
        cart.refresh_from_db()
        return Response(CartSerializer(cart, context=self.get_serializer_context()).data, status=status_code)

    def list(self, request):
        return self._render(self._cart(request, create=False))

    @action(detail=False, methods=["post"], url_path="add")
    def add_item(self, request):
        ser = AddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        product = CartService.resolve_product(data.get("product_type") or data.get("product_model"), data["product_id"])
        cart = self._cart(request)
        CartService(cart).add_item(product, quantity=data["quantity"])
        return self._render(cart, status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="update")
    def update_item(self, request, pk=None):
        ser = UpdateQuantitySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cart = self._cart(request, create=False)
        if cart is None:
            raise CartItemNotFoundError()
        CartService(cart).update_quantity(pk, ser.validated_data["quantity"])
        return self._render(cart)

    @action(detail=True, methods=["delete"], url_path="remove")
    def remove_item(self, request, pk=None):
        cart = self._cart(request, create=False)
        if cart is None:
            raise CartItemNotFoundError()
        CartService(cart).remove_item(pk)
        return self._render(cart)

    @action(detail=False, methods=["post"], url_path="clear")
    def clear_cart(self, request):
        cart = self._cart(request, create=False)
        if cart is not None:
            CartService(cart).clear()
        return self._render(cart)

    @action(detail=False, methods=["post"], url_path="coupon", throttle_scope="coupon")
    def apply_coupon(self, request):
        ser = ApplyCouponSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cart = self._cart(request)
        CartService(cart).apply_coupon(ser.validated_data["code"])
        return self._render(cart)

    @action(detail=False, methods=["delete"], url_path="coupon/remove")
    def remove_coupon(self, request):
        cart = self._cart(request, create=False)
        if cart is not None:
            CartService(cart).remove_coupon()
        return self._render(cart)

    @action(detail=False, methods=["post"], url_path="contact")
    def contact(self, request):
        ser = ContactSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cart = self._cart(request)
        CartService(cart).set_contact(**ser.validated_data)
        return self._render(cart)

    @action(detail=True, methods=["post"], url_path="save", permission_classes=[IsAuthenticated])
    def save_for_later(self, request, pk=None):
        cart = self._cart(request, create=False)
        if cart is None:
            raise CartItemNotFoundError()
        saved = CartService(cart).save_for_later(pk)
        return Response(SavedItemSerializer(saved, context={"display": display_map([saved])}).data)

    @action(detail=True, methods=["post"], url_path="restore", permission_classes=[IsAuthenticated])
    def restore_saved(self, request, pk=None):
        cart = self._cart(request)
        CartService(cart).restore_saved_item(pk)
        return self._render(cart)

    @action(detail=False, methods=["get"], url_path="saved", permission_classes=[IsAuthenticated])
    def saved_items(self, request):
        qs = SavedItem.objects.filter(user=request.user).order_by("-created_at")
        items = list(qs[:200])
        return Response(SavedItemSerializer(items, many=True, context={"display": display_map(items)}).data)

    @action(
        detail=False,
        methods=["delete"],
        url_path=r"saved/(?P<saved_id>[0-9a-f-]+)",
        permission_classes=[IsAuthenticated],
    )
    def delete_saved(self, request, saved_id=None):
        SavedItem.objects.filter(user=request.user, pk=saved_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
