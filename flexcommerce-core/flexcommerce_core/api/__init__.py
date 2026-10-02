"""
Shared API building blocks for every FlexCommerce package.

All FlexCommerce views inherit ``FlexCommerceAPIMixin`` so they render errors
consistently, paginate and throttle out of the box — without requiring any
``REST_FRAMEWORK`` configuration in the host project.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import exception_handler as drf_exception_handler

from ..conf import fc_setting, throttle_rate
from ..exceptions import FlexCommerceError


def exception_handler(exc, context):
    """
    DRF exception handler that also understands ``FlexCommerceError``.

    You may set it globally::

        REST_FRAMEWORK = {"EXCEPTION_HANDLER": "flexcommerce_core.api.exception_handler"}

    but FlexCommerce views already use it regardless.
    """
    if isinstance(exc, FlexCommerceError):
        return Response(exc.to_dict(), status=exc.status_code)
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "error_dict") else exc.messages
        return Response({"error": "validation_error", "detail": detail, "extra": {}}, status=400)
    if isinstance(exc, ObjectDoesNotExist):
        exc = Http404()
    if isinstance(exc, DjangoPermissionDenied):
        return Response(
            {"error": "permission_denied", "detail": str(exc) or "Permission denied.", "extra": {}},
            status=status.HTTP_403_FORBIDDEN,
        )
    return drf_exception_handler(exc, context)


class FlexPagination(PageNumberPagination):
    page_size_query_param = "page_size"

    def get_page_size(self, request):
        self.page_size = fc_setting("PAGE_SIZE", 20)
        self.max_page_size = fc_setting("MAX_PAGE_SIZE", 100)
        return super().get_page_size(request)


class FlexScopedThrottle(SimpleRateThrottle):
    """
    Scoped throttle reading its rate from ``FLEXCOMMERCE["THROTTLE_RATES"]``.
    Authenticated users are keyed by id, anonymous clients by IP.
    Disable globally with ``FLEXCOMMERCE["THROTTLING_ENABLED"] = False``.
    """

    scope = None

    def __init__(self):  # rate resolved lazily per request
        pass

    def allow_request(self, request, view):
        if not fc_setting("THROTTLING_ENABLED", True):
            return True
        self.scope = getattr(view, "throttle_scope", None) or self.scope
        self.rate = throttle_rate(self.scope) if self.scope else None
        if not self.rate:
            return True
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            ident = f"ip:{self.get_ident(request)}"
        return f"flexcommerce:throttle:{self.scope}:{ident}"


class FlexCommerceAPIMixin:
    """Mixin for all FlexCommerce API views."""

    pagination_class = FlexPagination
    throttle_scope = None

    def get_exception_handler(self):
        return exception_handler

    def get_throttles(self):
        throttles = list(super().get_throttles())
        if self.throttle_scope:
            throttles.append(FlexScopedThrottle())
        return throttles


class IsStaff(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsOwnerOrStaff(permissions.BasePermission):
    """Object-level: ``obj.user == request.user`` or staff."""

    owner_field = "user"

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        owner = getattr(obj, getattr(view, "owner_field", self.owner_field) + "_id", None)
        return owner is not None and owner == request.user.pk


class ReadOnlyOrStaff(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


def paginate(view, queryset, serializer_class, **serializer_kwargs):
    """Paginate ``queryset`` inside a custom ``@action``."""
    context = serializer_kwargs.pop("context", None) or view.get_serializer_context()
    page = view.paginate_queryset(queryset)
    if page is not None:
        data = serializer_class(page, many=True, context=context, **serializer_kwargs).data
        return view.get_paginated_response(data)
    return Response(serializer_class(queryset, many=True, context=context, **serializer_kwargs).data)
