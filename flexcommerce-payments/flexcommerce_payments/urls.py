from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    InitiatePaymentView,
    PaymentMethodsView,
    PaymentViewSet,
    VerifyPaymentView,
    WalletViewSet,
    WebhookView,
)

router = DefaultRouter()
router.include_root_view = False
router.register("payments", PaymentViewSet, basename="payment")
router.register("wallet", WalletViewSet, basename="wallet")

urlpatterns = [
    path("payments/methods/", PaymentMethodsView.as_view(), name="payment-methods-list"),
    path("payments/initiate/", InitiatePaymentView.as_view(), name="payment-initiate"),
    path("payments/verify/", VerifyPaymentView.as_view(), name="payment-verify"),
    path("payments/webhooks/<slug:provider>/", WebhookView.as_view(), name="payment-webhook"),
] + router.urls
