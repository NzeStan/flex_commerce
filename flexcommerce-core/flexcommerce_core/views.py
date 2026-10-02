"""Core API: customer address book."""

from django.db import transaction
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .api import FlexCommerceAPIMixin
from .models import Address
from .serializers import AddressSerializer


class AddressViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    """
    ``/addresses/`` — the authenticated user's address book.
    ``POST /addresses/{id}/set-default/`` makes an address the default for its type.
    """

    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def _ensure_single_default(self, address):
        if address.is_default:
            Address.objects.filter(user=address.user, address_type=address.address_type).exclude(pk=address.pk).update(
                is_default=False
            )

    @transaction.atomic
    def perform_create(self, serializer):
        has_any = Address.objects.filter(
            user=self.request.user,
            address_type=serializer.validated_data.get("address_type", "shipping"),
        ).exists()
        address = serializer.save(
            user=self.request.user,
            is_default=serializer.validated_data.get("is_default") or not has_any,
        )
        self._ensure_single_default(address)

    @transaction.atomic
    def perform_update(self, serializer):
        self._ensure_single_default(serializer.save())

    @action(detail=True, methods=["post"], url_path="set-default")
    @transaction.atomic
    def set_default(self, request, pk=None):
        address = self.get_object()
        address.is_default = True
        address.save(update_fields=["is_default", "updated_at"])
        self._ensure_single_default(address)
        return Response(self.get_serializer(address).data)
