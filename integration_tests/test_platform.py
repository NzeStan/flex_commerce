"""Platform-level checks with every app installed together."""

from io import StringIO

import pytest
from django.contrib import admin
from django.core.management import call_command
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_no_missing_migrations():
    call_command("makemigrations", "--check", "--dry-run", stdout=StringIO())


def test_system_checks_clean():
    call_command("check", stdout=StringIO())


def test_every_admin_changelist_and_add_page_loads(client, staff, catalog, vendor_product):
    client.force_login(staff)
    for model, model_admin in admin.site._registry.items():
        meta = model._meta
        if not meta.app_label.startswith("flexcommerce_"):
            continue
        url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
        assert client.get(url).status_code == 200, url
        if model_admin.has_add_permission(type("R", (), {"user": staff})()):
            add = reverse(f"admin:{meta.app_label}_{meta.model_name}_add")
            assert client.get(add).status_code == 200, add


def test_all_jobs_registered_and_runnable():
    from flexcommerce_core.jobs import get_jobs, run_due_jobs

    names = set(get_jobs())
    assert names == {
        "webhooks.retry",
        "inventory.release_expired",
        "cart.expire",
        "cart.abandoned",
        "orders.expire_unpaid",
        "payments.expire_stale",
        "engagement.price_drops",
        "marketplace.payouts",
        "notifications.retry",
        "analytics.daily_summary",
    }
    results = run_due_jobs(force=True)
    assert not [name for name, result in results.items() if isinstance(result, Exception)], results
    out = StringIO()
    call_command("flexcommerce_run_jobs", "--list", stdout=out)
    assert "orders.expire_unpaid" in out.getvalue()


@pytest.mark.parametrize(
    "path",
    [
        "/api/catalog/products/",
        "/api/catalog/categories/",
        "/api/catalog/brands/",
        "/api/pricing/tax-categories/",
        "/api/shipping/states/",
        "/api/shipping/pickup-stations/",
        "/api/cart/",
        "/api/checkout/payment-methods/",
        "/api/flash-sales/active/",
        "/api/reviews/",
        "/api/questions/",
        "/api/recent-searches/trending/",
        "/api/vendors/",
        "/api/payments/methods/",
        "/api/recently-viewed/",
    ],
)
def test_public_endpoints_are_mounted(client, path):
    assert client.get(path).status_code == 200, path


@pytest.mark.parametrize(
    "path",
    [
        "/api/orders/",
        "/api/wallet/",
        "/api/addresses/",
        "/api/wishlists/",
        "/api/notifications/inbox/",
        "/api/notifications/preferences/",
    ],
)
def test_private_endpoints_require_login(client, path):
    assert client.get(path).status_code in (401, 403), path


@pytest.mark.parametrize(
    "path",
    ["/api/inventory/", "/api/coupons/", "/api/analytics/dashboard/", "/api/payouts/", "/api/notifications/logs/"],
)
def test_staff_endpoints_reject_customers(client, customer, path):
    client.force_login(customer)
    assert client.get(path).status_code == 403, path
