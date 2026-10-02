"""
Discounts API.

Staff:   /coupons/ CRUD, /coupons/{id}/usages/, /flash-sales/ CRUD,
         POST /flash-sales/{id}/items/  (add a product at a sale price / with a cap)
Public:  POST /coupons/validate/        {code, cart_total?}  (throttled)
         GET  /flash-sales/active/
"""

from django.contrib.contenttypes.models import ContentType
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from flexcommerce_core.api import FlexCommerceAPIMixin, FlexScopedThrottle, IsStaff, paginate
from flexcommerce_core.exceptions import DiscountError
from flexcommerce_core.utils.products import get_product
from flexcommerce_core.utils.vat import round_price

from . import services
from .models import Coupon, FlashSale, FlashSaleItem
from .serializers import (
    CouponSerializer,
    CouponUsageSerializer,
    CouponValidateSerializer,
    FlashSaleItemSerializer,
    FlashSaleItemWriteSerializer,
    FlashSaleSerializer,
)


class CouponThrottle(FlexScopedThrottle):
    scope = "coupon"


class CouponViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    queryset = Coupon.objects.all().order_by("-created_at")
    serializer_class = CouponSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("search"):
            qs = qs.filter(code__icontains=self.request.query_params["search"][:100])
        return qs

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        throttle_classes=[CouponThrottle],
    )
    def validate(self, request):
        """Quick check of a code against a cart total (no product restrictions)."""
        ser = CouponValidateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            coupon = services.get_coupon(ser.validated_data["code"])
            user = request.user if request.user.is_authenticated else None
            services.check_coupon(coupon, user=user)
            total = ser.validated_data.get("cart_total")
            if total is not None and total < coupon.minimum_cart_value:
                raise DiscountError(
                    "Cart total does not meet the minimum required for this coupon.",
                    code="coupon_minimum_value",
                    extra={"minimum": str(round_price(coupon.minimum_cart_value))},
                )
        except DiscountError as exc:
            return Response({"valid": False, **exc.to_dict()})
        data = {
            "valid": True,
            "coupon_code": coupon.code,
            "coupon_type": coupon.coupon_type,
            "free_shipping": coupon.coupon_type == Coupon.TYPE_FREE_SHIPPING,
            "minimum_cart_value": str(round_price(coupon.minimum_cart_value)),
        }
        if total is not None:
            data["discount_amount"] = str(coupon.calculate_discount(total))
        return Response(data)

    @action(detail=True, methods=["get"])
    def usages(self, request, pk=None):
        return paginate(self, self.get_object().usages.order_by("-created_at"), CouponUsageSerializer)


class FlashSaleViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    queryset = FlashSale.objects.prefetch_related("items__content_type").order_by("-starts_at")
    serializer_class = FlashSaleSerializer
    permission_classes = [IsStaff]

    @action(detail=False, methods=["get"], permission_classes=[permissions.AllowAny])
    def active(self, request):
        sales = services.active_flash_sales().prefetch_related("items__content_type")
        return Response(FlashSaleSerializer(sales, many=True).data)

    @action(detail=True, methods=["post"])
    def items(self, request, pk=None):
        sale = self.get_object()
        ser = FlashSaleItemWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        product = get_product(data.get("product_type"), data["product_id"], for_purchase=False)
        item, _ = FlashSaleItem.objects.update_or_create(
            sale=sale,
            content_type=ContentType.objects.get_for_model(product),
            object_id=product.pk,
            defaults={
                "sale_price": data.get("sale_price"),
                "quantity_limit": data.get("quantity_limit"),
            },
        )
        services.invalidate_caches()
        return Response(FlashSaleItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path=r"items/(?P<item_id>[0-9a-f-]+)")
    def remove_item(self, request, pk=None, item_id=None):
        FlashSaleItem.objects.filter(sale=self.get_object(), pk=item_id).delete()
        services.invalidate_caches()
        return Response(status=status.HTTP_204_NO_CONTENT)
