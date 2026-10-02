"""
Analytics API (staff only). Every endpoint accepts ``start_date`` / ``end_date``
(YYYY-MM-DD, default: last 30 days; max range 3 years).

  GET /analytics/dashboard/
  GET /analytics/revenue/?period=daily|weekly|monthly
  GET /analytics/top-products/?by=revenue|quantity&limit=10
  GET /analytics/abandoned-carts/
  GET /analytics/conversion/
  GET /analytics/coupon-performance/
  GET /analytics/vendor-performance/
  GET /analytics/sales-by-state/
  GET /analytics/payment-methods/
  GET /analytics/customers/
  GET /analytics/daily-summary/
  GET /analytics/export/?report=<name>   CSV download
"""

import csv
from datetime import timedelta

from django.http import StreamingHttpResponse
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from flexcommerce_core.api import FlexCommerceAPIMixin, IsStaff
from flexcommerce_core.utils.csv import safe_dict

from . import reports
from .models import DailyRevenueSummary
from .serializers import DailyRevenueSummarySerializer

MAX_RANGE_DAYS = 3 * 366


class RangeSerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    period = serializers.ChoiceField(choices=list(reports.PERIODS), required=False, default="daily")
    by = serializers.ChoiceField(choices=["revenue", "quantity"], required=False, default="revenue")
    limit = serializers.IntegerField(min_value=1, max_value=500, required=False, default=10)
    report = serializers.ChoiceField(choices=list(reports.REPORTS), required=False, default="revenue")

    def validate(self, attrs):
        from django.utils import timezone

        end = attrs.get("end_date") or timezone.localdate()
        start = attrs.get("start_date") or end - timedelta(days=30)
        if start > end:
            raise serializers.ValidationError("start_date must be before end_date.")
        if (end - start).days > MAX_RANGE_DAYS:
            raise serializers.ValidationError("Date range is too large (max 3 years).")
        attrs["start_date"], attrs["end_date"] = start, end
        return attrs


class ReportView(FlexCommerceAPIMixin, APIView):
    permission_classes = [IsStaff]
    report = None

    def params(self, request):
        ser = RangeSerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        return ser.validated_data

    def get(self, request):
        p = self.params(request)
        data = self.run(p)
        return Response(
            {
                "start_date": p["start_date"].isoformat(),
                "end_date": p["end_date"].isoformat(),
                **(data if isinstance(data, dict) else {"results": data}),
            }
        )

    def run(self, p):
        return getattr(reports, self.report)(p["start_date"], p["end_date"])


class RevenueDashboardView(ReportView):
    def run(self, p):
        return {
            "period": p["period"],
            "results": reports.revenue_report(p["start_date"], p["end_date"], p["period"]),
        }


class TopProductsView(ReportView):
    def run(self, p):
        return {
            "ranked_by": p["by"],
            "results": reports.top_products_report(p["start_date"], p["end_date"], limit=p["limit"], by=p["by"]),
        }


class AbandonedCartReportView(ReportView):
    report = "abandoned_cart_report"


class ConversionView(ReportView):
    report = "conversion_report"


class CouponPerformanceView(ReportView):
    report = "coupon_performance_report"


class VendorPerformanceView(ReportView):
    report = "vendor_performance_report"


class SalesByStateView(ReportView):
    report = "sales_by_state_report"


class PaymentMethodsView(ReportView):
    report = "payment_methods_report"


class CustomersView(ReportView):
    report = "customer_report"


class DashboardView(FlexCommerceAPIMixin, APIView):
    permission_classes = [IsStaff]

    def get(self, request):
        return Response(reports.dashboard_summary())


class DailySummaryView(ReportView):
    def get(self, request):
        p = self.params(request)
        qs = DailyRevenueSummary.objects.filter(date__gte=p["start_date"], date__lte=p["end_date"]).order_by("-date")
        return Response(DailyRevenueSummarySerializer(qs, many=True).data)


class _Echo:
    def write(self, value):
        return value


class ExportCSVView(ReportView):
    def get(self, request):
        p = self.params(request)
        name = p["report"]
        if name == "revenue":
            rows = reports.revenue_report(p["start_date"], p["end_date"], p["period"])
        elif name == "top_products":
            rows = reports.top_products_report(p["start_date"], p["end_date"], limit=p["limit"], by=p["by"])
        else:
            rows = reports.REPORTS[name](p["start_date"], p["end_date"])
        if not rows:
            return Response({"detail": "No data for the selected date range.", "results": []})
        writer = csv.DictWriter(_Echo(), fieldnames=list(rows[0].keys()))

        def stream():
            yield writer.writeheader()
            for row in rows:
                yield writer.writerow(safe_dict(row))

        response = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="fc_{name}_{p["start_date"]}_{p["end_date"]}.csv"'
        return response
