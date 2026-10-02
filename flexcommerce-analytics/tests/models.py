"""Test product model for the orders test-suite."""

import uuid
from decimal import Decimal

from django.db import models


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, default="Test Product")
    sku = models.CharField(max_length=100, default="SKU")
    price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("1000.00"))
    vendor_id = models.UUIDField(null=True, blank=True)

    class Meta:
        app_label = "tests"

    def order_snapshot(self):
        return {"image": f"https://cdn.example.com/{self.sku}.jpg"}
