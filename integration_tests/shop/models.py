"""A custom user model — the integration suite proves FlexCommerce never assumes ``auth.User``."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class Customer(AbstractUser):
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        app_label = "shop"
