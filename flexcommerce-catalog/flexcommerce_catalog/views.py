"""
Catalog API.

Public (read):
  GET /catalog/categories/            flat list (``?tree=1`` nested, cached)
  GET /catalog/categories/{slug}/
  GET /catalog/brands/
  GET /catalog/products/              search / filter / sort / paginate
  GET /catalog/products/{slug|id}/    detail with variants, images, breadcrumbs
  GET /catalog/products/{slug}/related/
  GET /catalog/products/facets/       price range + brands for the current filters

Write: staff for categories/brands; staff or approved marketplace vendors
(own products only) for products, variants and images.
"""

import re
import uuid
from decimal import Decimal

from django.core.cache import cache
from django.db.models import F, Prefetch, Q
from django.utils.module_loading import import_string
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from flexcommerce_core import hooks
from flexcommerce_core.api import FlexCommerceAPIMixin, ReadOnlyOrStaff
from flexcommerce_core.utils.vat import to_decimal

from .conf import catalog_setting
from .models import CATEGORY_TREE_CACHE_KEY, Brand, Category, Product, ProductImage, ProductVariant
from .serializers import (
    BrandSerializer,
    CategorySerializer,
    ProductDetailSerializer,
    ProductImageSerializer,
    ProductListSerializer,
    ProductWriteSerializer,
    StaffVariantSerializer,
    VariantSerializer,
)

ATTRIBUTE_KEY = re.compile(r"^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*$")  # no "__" lookups

ORDERING = {
    "newest": ("-published_at", "-created_at"),
    "price": ("min_price", "-created_at"),
    "-price": ("-min_price", "-created_at"),
    "popularity": ("-sold_count", "-rating_count"),
    "rating": ("-rating_avg", "-rating_count"),
    "name": ("name",),
    "discount": ("-compare_at_price",),
}


def vendor_id_for(user):
    """Vendor UUID the user may manage products for (via the marketplace hook)."""
    if not user or not user.is_authenticated:
        return None
    for vendor_id in hooks.run("catalog.vendor_id_for_user", user):
        if vendor_id:
            return vendor_id
    return None


class CanManageProducts(permissions.BasePermission):
    """Read for everyone; write for staff or approved vendors (own products only)."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and (request.user.is_staff or vendor_id_for(request.user)))

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS or request.user.is_staff:
            return True
        product = obj if isinstance(obj, Product) else obj.product
        return product.vendor_id is not None and product.vendor_id == vendor_id_for(request.user)


class CategoryViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [ReadOnlyOrStaff]
    lookup_field = "slug"
    pagination_class = None

    def get_queryset(self):
        qs = Category.objects.all()
        if not self.request.user.is_staff:
            qs = qs.filter(is_active=True)
        parent = self.request.query_params.get("parent")
        if parent == "root":
            qs = qs.filter(parent__isnull=True)
        elif parent:
            qs = qs.filter(parent__slug=parent)
        return qs

    def list(self, request, *args, **kwargs):
        if request.query_params.get("tree") in ("1", "true"):
            return Response(self._tree())
        return super().list(request, *args, **kwargs)

    @staticmethod
    def _tree():
        tree = cache.get(CATEGORY_TREE_CACHE_KEY)
        if tree is None:
            nodes = {}
            roots = []
            for c in Category.objects.filter(is_active=True).order_by("depth", "sort_order", "name"):
                node = {"id": str(c.pk), "name": c.name, "slug": c.slug, "image_url": c.image_url, "children": []}
                nodes[c.pk] = node
                if c.parent_id is None:
                    roots.append(node)
                elif c.parent_id in nodes:
                    nodes[c.parent_id]["children"].append(node)
            tree = roots
            cache.set(CATEGORY_TREE_CACHE_KEY, tree, catalog_setting("CATALOG_CATEGORY_CACHE_SECONDS"))
        return tree


class BrandViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    serializer_class = BrandSerializer
    permission_classes = [ReadOnlyOrStaff]
    lookup_field = "slug"

    def get_queryset(self):
        qs = Brand.objects.all()
        if not self.request.user.is_staff:
            qs = qs.filter(is_active=True)
        if self.request.query_params.get("official") in ("1", "true"):
            qs = qs.filter(is_official_store=True)
        return qs


class ProductViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    permission_classes = [CanManageProducts]
    lookup_field = "slug"
    lookup_value_regex = "[^/]+"

    # ── querysets ─────────────────────────────────────────────────────────────
    def _base_queryset(self):
        user = self.request.user
        qs = Product.objects.select_related("brand").prefetch_related(
            Prefetch("images", queryset=ProductImage.objects.order_by("-is_primary", "sort_order", "created_at"))
        )
        if user.is_staff:
            return qs
        vendor_id = vendor_id_for(user)
        if vendor_id and self.request.query_params.get("mine") in ("1", "true"):
            return qs.filter(vendor_id=vendor_id)
        if vendor_id and self.action not in ("list", "retrieve", "related", "facets"):
            return qs.filter(vendor_id=vendor_id)
        return qs.filter(status=Product.STATUS_ACTIVE)

    def get_queryset(self):
        qs = self._base_queryset()
        if self.action in ("list", "facets"):
            qs = self.filter_queryset_params(qs)
        if self.action == "retrieve":
            qs = qs.prefetch_related("categories", "variants")
        return qs

    def filter_queryset_params(self, qs, include_price=True):
        params = self.request.query_params
        category = params.get("category")
        if category:
            cat = Category.objects.filter(slug=category).first()
            qs = qs.in_category(cat) if cat else qs.none()
        brands = [b for b in params.get("brand", "").split(",") if b]
        if brands:
            qs = qs.filter(brand__slug__in=brands)
        if include_price:
            try:
                if params.get("min_price"):
                    qs = qs.filter(min_price__gte=to_decimal(params["min_price"]))
                if params.get("max_price"):
                    qs = qs.filter(min_price__lte=to_decimal(params["max_price"]))
                if params.get("rating"):
                    qs = qs.filter(rating_avg__gte=to_decimal(params["rating"]))
            except ValueError:
                raise ValidationError({"detail": "Invalid numeric filter."}) from None
        if params.get("in_stock") in ("1", "true"):
            qs = qs.filter(in_stock=True)
        if params.get("featured") in ("1", "true"):
            qs = qs.filter(is_featured=True)
        if params.get("on_sale") in ("1", "true"):
            qs = qs.filter(compare_at_price__gt=F("min_price"))
        if params.get("vendor"):
            try:
                qs = qs.filter(vendor_id=uuid.UUID(params["vendor"]))
            except ValueError:
                qs = qs.none()
        if params.get("status") and self.request.user.is_staff:
            qs = qs.filter(status=params["status"])
        for key, value in params.items():
            attr = key[5:]
            if key.startswith("attr_") and value and ATTRIBUTE_KEY.match(attr):
                qs = qs.filter(**{f"variants__attributes__{attr}": value, "variants__is_active": True})
        search = params.get("q", "").strip()[:200]
        if search:
            qs = import_string(catalog_setting("CATALOG_SEARCH_HANDLER"))(qs, search)
        ordering = params.get("ordering")
        if ordering in ORDERING:
            qs = qs.order_by(*ORDERING[ordering])
        elif not search:
            qs = qs.order_by(*ORDERING["newest"])
        return qs.distinct() if brands or category else qs

    def get_object(self):
        lookup = self.kwargs[self.lookup_field]
        qs = self.get_queryset()
        try:
            obj = qs.get(pk=uuid.UUID(lookup))
        except (ValueError, Product.DoesNotExist):
            obj = qs.filter(slug=lookup).first()
            if obj is None:
                from django.http import Http404

                raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ProductWriteSerializer
        if self.action == "retrieve":
            return ProductDetailSerializer
        return ProductListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        user = self.request.user
        context["show_cost"] = bool(user and user.is_staff)
        context["show_inactive"] = bool(user and (user.is_staff or vendor_id_for(user)))
        return context

    def retrieve(self, request, *args, **kwargs):
        from flexcommerce_core.utils.products import is_installed

        product = self.get_object()
        context = self.get_serializer_context()
        if is_installed("flexcommerce_inventory"):
            from flexcommerce_inventory.services import availability_map

            context["stock_map"] = {
                pk: available is None or available > 0
                for pk, available in availability_map(list(product.variants.all())).items()
            }
        return Response(ProductDetailSerializer(product, context=context).data)

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_staff:
            vendor = self.request.data.get("vendor_id")
            serializer.save(vendor_id=vendor or None)
        else:
            serializer.save(vendor_id=vendor_id_for(user))

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        product = Product.objects.get(pk=response.data["id"])
        response.data = ProductDetailSerializer(product, context=self.get_serializer_context()).data
        return response

    # ── extra endpoints ──────────────────────────────────────────────────────
    @action(detail=True, methods=["get"])
    def related(self, request, slug=None):
        product = self.get_object()
        category_ids = list(product.categories.values_list("pk", flat=True))
        qs = Product.objects.active().exclude(pk=product.pk).select_related("brand").prefetch_related("images")
        qs = qs.filter(Q(categories__in=category_ids) | Q(brand_id=product.brand_id, brand__isnull=False)).distinct()
        qs = qs.order_by("-sold_count", "-rating_avg")[: catalog_setting("CATALOG_RELATED_LIMIT")]
        return Response(ProductListSerializer(qs, many=True, context=self.get_serializer_context()).data)

    @action(detail=False, methods=["get"])
    def facets(self, request):
        from django.db.models import Count, Max, Min

        qs = self.filter_queryset_params(self._base_queryset(), include_price=False).order_by()
        prices = qs.aggregate(lo=Min("min_price"), hi=Max("min_price"))
        brands = (
            Brand.objects.filter(products__in=qs.values("pk"))
            .values("slug", "name")
            .annotate(count=Count("products", distinct=True))
            .order_by("-count", "name")[:50]
        )
        return Response(
            {
                "price": {"min": prices["lo"] or Decimal("0.00"), "max": prices["hi"] or Decimal("0.00")},
                "brands": list(brands),
            }
        )


class VariantViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    """Staff/vendor management of variants: ``?product=<id>`` to filter."""

    permission_classes = [permissions.IsAuthenticated, CanManageProducts]

    def get_serializer_class(self):
        return StaffVariantSerializer if self.request.user.is_staff else VariantSerializer

    def get_queryset(self):
        qs = ProductVariant.objects.select_related("product")
        user = self.request.user
        if not user.is_staff:
            qs = qs.filter(product__vendor_id=vendor_id_for(user))
        if self.request.query_params.get("product"):
            qs = qs.filter(product_id=self.request.query_params["product"])
        return qs

    def perform_create(self, serializer):
        product = serializer.validated_data.get("product")
        if product is None:
            raise ValidationError({"product": "This field is required."})
        if not self.request.user.is_staff and product.vendor_id != vendor_id_for(self.request.user):
            raise PermissionDenied("You cannot add variants to this product.")
        serializer.save()


class ProductImageViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    serializer_class = ProductImageSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageProducts]

    def get_queryset(self):
        qs = ProductImage.objects.select_related("product")
        user = self.request.user
        if not user.is_staff:
            qs = qs.filter(product__vendor_id=vendor_id_for(user))
        if self.request.query_params.get("product"):
            qs = qs.filter(product_id=self.request.query_params["product"])
        return qs

    def perform_create(self, serializer):
        product = serializer.validated_data.get("product")
        if product is None:
            raise ValidationError({"product": "This field is required."})
        if not self.request.user.is_staff and product.vendor_id != vendor_id_for(self.request.user):
            raise PermissionDenied("You cannot add images to this product.")
        image = serializer.save()
        if image.is_primary:
            ProductImage.objects.filter(product=product).exclude(pk=image.pk).update(is_primary=False)
