from django.urls import path

from .views import (
    AbandonedCartReportView,
    ConversionView,
    CouponPerformanceView,
    CustomersView,
    DailySummaryView,
    DashboardView,
    ExportCSVView,
    PaymentMethodsView,
    RevenueDashboardView,
    SalesByStateView,
    TopProductsView,
    VendorPerformanceView,
)

urlpatterns = [
    path("analytics/dashboard/", DashboardView.as_view(), name="analytics-dashboard"),
    path("analytics/revenue/", RevenueDashboardView.as_view(), name="analytics-revenue"),
    path("analytics/top-products/", TopProductsView.as_view(), name="analytics-top-products"),
    path(
        "analytics/abandoned-carts/",
        AbandonedCartReportView.as_view(),
        name="analytics-abandoned-carts",
    ),
    path("analytics/conversion/", ConversionView.as_view(), name="analytics-conversion"),
    path(
        "analytics/coupon-performance/",
        CouponPerformanceView.as_view(),
        name="analytics-coupon-perf",
    ),
    path(
        "analytics/vendor-performance/",
        VendorPerformanceView.as_view(),
        name="analytics-vendor-perf",
    ),
    path("analytics/sales-by-state/", SalesByStateView.as_view(), name="analytics-sales-by-state"),
    path("analytics/payment-methods/", PaymentMethodsView.as_view(), name="analytics-payment-methods"),
    path("analytics/customers/", CustomersView.as_view(), name="analytics-customers"),
    path("analytics/daily-summary/", DailySummaryView.as_view(), name="analytics-daily-summary"),
    path("analytics/export/", ExportCSVView.as_view(), name="analytics-export"),
]
