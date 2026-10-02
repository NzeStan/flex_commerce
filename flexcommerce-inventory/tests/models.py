"""Test product model for inventory package tests."""

import uuid

from django.db import models


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, default="Test Product")
    sku = models.CharField(max_length=100, default="INV-001")

    class Meta:
        app_label = "tests"
