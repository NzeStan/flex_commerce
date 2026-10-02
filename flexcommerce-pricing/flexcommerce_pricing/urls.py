from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import PriceCalculateView, PriceQuoteView, TaxCategoryViewSet

router = DefaultRouter()
router.include_root_view = False
router.register("pricing/tax-categories", TaxCategoryViewSet, basename="pricing-tax-category")

urlpatterns = [
    path("pricing/calculate/", PriceCalculateView.as_view(), name="pricing-calculate"),
    path("pricing/quote/", PriceQuoteView.as_view(), name="pricing-quote"),
] + router.urls
