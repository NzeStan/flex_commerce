"""Test product model for cart package tests."""

import uuid
from decimal import Decimal

from django.db import models


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, default="Test Product")
    price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("1000.00"))
    sku = models.CharField(max_length=100, default="TEST-001")
    stock = models.IntegerField(default=100)
    vat_exempt = models.BooleanField(default=False)
    vat_zero_rated = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "tests"
