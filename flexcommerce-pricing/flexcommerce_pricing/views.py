"""
Pricing API.

  POST /pricing/calculate/       VAT breakdown for an arbitrary amount
  POST /pricing/quote/           server-side prices for products (flash sales, tax...)
  GET  /pricing/tax-categories/  list (staff: full CRUD)
"""

from rest_framework import permissions, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from flexcommerce_core.api import FlexCommerceAPIMixin, ReadOnlyOrStaff
from flexcommerce_core.conf import fc_setting
from flexcommerce_core.utils.pricing import price_product
from flexcommerce_core.utils.products import get_product, product_name, product_type_label
from flexcommerce_core.utils.vat import ZERO, compute_tax, format_currency, round_price

from .models import TaxCategory
from .serializers import PriceCalculateSerializer, QuoteSerializer, TaxCategorySerializer


class PriceCalculateView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = PriceCalculateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        inclusive = data.get("inclusive")
        if inclusive is None:
            inclusive = fc_setting("VAT_INCLUSIVE", False)
        result = compute_tax(data["price"], rate=data.get("rate"), inclusive=inclusive)
        return Response(
            {
                "net": str(result["net"]),
                "vat": str(result["vat"]),
                "gross": str(result["gross"]),
                "rate": str(result["rate"]),
                "inclusive": inclusive,
                "currency": fc_setting("CURRENCY", "NGN"),
                "formatted_gross": format_currency(result["gross"]),
            }
        )


class PriceQuoteView(FlexCommerceAPIMixin, APIView):
    """Authoritative prices for a list of products (what the cart will charge)."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = QuoteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = request.user if request.user.is_authenticated else None
        lines, subtotal, vat = [], ZERO, ZERO
        for item in ser.validated_data["items"]:
            product = get_product(item.get("product_type"), item["product_id"], for_purchase=False)
            amounts = price_product(product, quantity=item["quantity"], user=user)
            subtotal += amounts["line_gross"]
            vat += amounts["line_vat"]
            lines.append(
                {
                    "product_type": product_type_label(product),
                    "product_id": str(product.pk),
                    "name": product_name(product),
                    "quantity": item["quantity"],
                    **{k: str(v) for k, v in amounts.items()},
                }
            )
        return Response(
            {
                "currency": fc_setting("CURRENCY", "NGN"),
                "items": lines,
                "subtotal": str(round_price(subtotal)),
                "vat": str(round_price(vat)),
            }
        )


class TaxCategoryViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    queryset = TaxCategory.objects.all()
    serializer_class = TaxCategorySerializer
    permission_classes = [ReadOnlyOrStaff]
    lookup_field = "code"
    pagination_class = None


# Alias kept for anyone importing it directly.
TaxCategoryListView = TaxCategoryViewSet
