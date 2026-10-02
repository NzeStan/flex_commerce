"""
FlexCommerce master URL configuration. Include it once::

    urlpatterns = [path("api/", include("flexcommerce_core.urls"))]

Every FlexCommerce app that is in ``INSTALLED_APPS`` is mounted automatically.
Apps that are pip-installed but not in ``INSTALLED_APPS`` are skipped, so each
feature stays optional. You can also include any package's ``urls`` yourself.
"""

from django.apps import apps
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AddressViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("addresses", AddressViewSet, basename="address")

urlpatterns = list(router.urls)

PACKAGES = [
    "flexcommerce_catalog",
    "flexcommerce_cart",
    "flexcommerce_pricing",
    "flexcommerce_inventory",
    "flexcommerce_discounts",
    "flexcommerce_shipping",
    "flexcommerce_orders",
    "flexcommerce_checkout",
    "flexcommerce_payments",
    "flexcommerce_engagement",
    "flexcommerce_marketplace",
    "flexcommerce_notifications",
    "flexcommerce_analytics",
]

for app_name in PACKAGES:
    if apps.is_installed(app_name):
        urlpatterns.append(path("", include(f"{app_name}.urls")))
