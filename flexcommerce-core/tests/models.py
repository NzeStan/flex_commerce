"""Concrete models used only by the core test-suite."""

import uuid
from decimal import Decimal

from django.db import models

from flexcommerce_core.models import SoftDeleteModel


class SoftItem(SoftDeleteModel):
    name = models.CharField(max_length=100, default="test")

    class Meta:
        app_label = "tests"


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, default="Widget")
    sku = models.CharField(max_length=50, blank=True)
    price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("1000.00"))
    is_active = models.BooleanField(default=True)
    vat_exempt = models.BooleanField(default=False)

    class Meta:
        app_label = "tests"


class IntPKModel(models.Model):
    name = models.CharField(max_length=10)

    class Meta:
        app_label = "tests"
