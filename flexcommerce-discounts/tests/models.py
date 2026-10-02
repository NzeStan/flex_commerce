"""Test product model for the discounts test-suite."""

import uuid
from decimal import Decimal

from django.db import models


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, default="Test Product")
    price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("1000.00"))
    category_id = models.CharField(max_length=36, blank=True)
    vendor_id = models.UUIDField(null=True, blank=True)

    class Meta:
        app_label = "tests"
