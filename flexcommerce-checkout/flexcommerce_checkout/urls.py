from django.urls import path

from .views import CheckoutView, PaymentMethodsView, PreviewView, ShippingOptionsView

urlpatterns = [
    path("checkout/", CheckoutView.as_view(), name="checkout"),
    path("checkout/preview/", PreviewView.as_view(), name="checkout-preview"),
    path(
        "checkout/shipping-options/",
        ShippingOptionsView.as_view(),
        name="checkout-shipping-options",
    ),
    path("checkout/payment-methods/", PaymentMethodsView.as_view(), name="payment-methods"),
]
