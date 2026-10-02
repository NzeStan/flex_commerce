from io import StringIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.http import Http404
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from flexcommerce_core.api import (
    FlexCommerceAPIMixin,
    IsOwnerOrStaff,
    ReadOnlyOrStaff,
    exception_handler,
)
from flexcommerce_core.exceptions import CartLockedError, InsufficientStockError, NotFoundError
from flexcommerce_core.models import Address

ADDRESS = {
    "first_name": "Ada",
    "last_name": "Obi",
    "line1": "1 Allen Ave",
    "city": "Ikeja",
    "state": "Lagos",
    "phone": "08031234567",
}


class TestExceptionHandler:
    def test_flexcommerce_error(self):
        resp = exception_handler(InsufficientStockError(available=1, requested=3), {})
        assert resp.status_code == 400
        assert resp.data == {
            "error": "insufficient_stock",
            "detail": "Insufficient stock.",
            "extra": {"available": 1, "requested": 3},
        }
        assert exception_handler(CartLockedError(), {}).status_code == 409
        assert exception_handler(NotFoundError("gone"), {}).data["detail"] == "gone"

    def test_django_validation_error(self):
        resp = exception_handler(ValidationError({"field": ["bad"]}), {})
        assert resp.status_code == 400 and resp.data["detail"] == {"field": ["bad"]}
        resp = exception_handler(ValidationError("plain"), {})
        assert resp.data["detail"] == ["plain"]

    def test_permission_and_404(self):
        assert exception_handler(PermissionDenied(), {}).status_code == 403
        assert exception_handler(Http404(), {}).status_code == 404
        from django.contrib.auth.models import User

        assert exception_handler(User.DoesNotExist(), {}).status_code == 404

    def test_unknown_exception_passthrough(self):
        assert exception_handler(RuntimeError(), {}) is None


class Throttled(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "checkout"

    def get(self, request):
        return Response({"ok": True})


class Raising(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        raise InsufficientStockError(available=0, requested=1)


@pytest.mark.django_db
class TestMixin:
    def test_views_render_fc_errors(self):
        resp = Raising.as_view()(APIRequestFactory().get("/"))
        assert resp.status_code == 400 and resp.data["error"] == "insufficient_stock"

    def test_scoped_throttle(self, fc):
        from django.core.cache import cache

        cache.clear()
        fc(THROTTLE_RATES={"checkout": "2/minute"})
        view = Throttled.as_view()
        codes = [view(APIRequestFactory().get("/", REMOTE_ADDR="1.2.3.4")).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        # different client is not affected
        assert view(APIRequestFactory().get("/", REMOTE_ADDR="5.6.7.8")).status_code == 200

    def test_throttle_disabled(self, fc):
        fc(THROTTLE_RATES={"checkout": "1/minute"}, THROTTLING_ENABLED=False)
        view = Throttled.as_view()
        assert all(view(APIRequestFactory().get("/")).status_code == 200 for _ in range(3))

    def test_throttle_without_rate(self, fc):
        fc(THROTTLE_RATES={"checkout": None})
        view = Throttled.as_view()
        assert all(view(APIRequestFactory().get("/")).status_code == 200 for _ in range(3))


class TestPermissions:
    def test_owner_or_staff(self):
        from types import SimpleNamespace

        perm = IsOwnerOrStaff()
        req = SimpleNamespace(user=SimpleNamespace(is_staff=False, pk=1))
        assert perm.has_object_permission(req, None, SimpleNamespace(user_id=1))
        assert not perm.has_object_permission(req, None, SimpleNamespace(user_id=2))
        assert not perm.has_object_permission(req, None, SimpleNamespace())
        staff = SimpleNamespace(user=SimpleNamespace(is_staff=True, pk=5))
        assert perm.has_object_permission(staff, None, SimpleNamespace(user_id=1))

    def test_read_only_or_staff(self):
        from types import SimpleNamespace

        perm = ReadOnlyOrStaff()
        assert perm.has_permission(SimpleNamespace(method="GET", user=None), None)
        assert not perm.has_permission(SimpleNamespace(method="POST", user=SimpleNamespace(is_staff=False)), None)


@pytest.mark.django_db
class TestAddressAPI:
    url = "/api/addresses/"

    def test_requires_auth(self, api_client):
        assert api_client.get(self.url).status_code in (401, 403)

    def test_first_address_becomes_default(self, auth_client, user):
        resp = auth_client.post(self.url, ADDRESS, format="json")
        assert resp.status_code == 201, resp.data
        assert resp.data["is_default"] is True
        second = auth_client.post(self.url, {**ADDRESS, "label": "Office"}, format="json")
        assert second.data["is_default"] is False

    def test_single_default(self, auth_client, user):
        a = auth_client.post(self.url, ADDRESS, format="json").data
        b = auth_client.post(self.url, {**ADDRESS, "is_default": True}, format="json").data
        assert Address.objects.get(pk=a["id"]).is_default is False
        assert Address.objects.get(pk=b["id"]).is_default is True
        resp = auth_client.post(f"{self.url}{a['id']}/set-default/")
        assert resp.status_code == 200 and resp.data["is_default"] is True
        assert Address.objects.filter(user=user, is_default=True).count() == 1
        auth_client.patch(f"{self.url}{b['id']}/", {"is_default": True}, format="json")
        assert Address.objects.filter(user=user, is_default=True).get().pk.hex == b["id"].replace("-", "")

    def test_validates_phone(self, auth_client):
        resp = auth_client.post(self.url, {**ADDRESS, "phone": "123"}, format="json")
        assert resp.status_code == 400 and "phone" in resp.data

    def test_cannot_see_other_users_addresses(self, auth_client, other_user):
        other = Address.objects.create(user=other_user, **ADDRESS)
        assert auth_client.get(self.url).data["count"] == 0
        assert auth_client.get(f"{self.url}{other.pk}/").status_code == 404
        assert auth_client.delete(f"{self.url}{other.pk}/").status_code == 404

    def test_paginated_list(self, auth_client, user):
        for _ in range(3):
            Address.objects.create(user=user, **ADDRESS)
        data = auth_client.get(self.url + "?page_size=2").data
        assert data["count"] == 3 and len(data["results"]) == 2


@pytest.mark.django_db
class TestInfrastructure:
    def test_migrations_are_complete(self):
        out = StringIO()
        call_command("makemigrations", "flexcommerce_core", "--check", "--dry-run", stdout=out)

    def test_admin_pages_render(self, client, admin_user):
        from flexcommerce_core.models import AuditLog, WebhookDelivery, WebhookEndpoint

        client.force_login(admin_user)
        ep = WebhookEndpoint.objects.create(url="https://e.com/x", event="*")
        d = WebhookDelivery.objects.create(endpoint=ep, event="order.created", payload={})
        Address.objects.create(user=admin_user, **ADDRESS)
        AuditLog.log(ep, AuditLog.ACTION_CREATE)
        for model in ("auditlog", "webhookendpoint", "webhookdelivery", "address"):
            assert client.get(f"/admin/flexcommerce_core/{model}/").status_code == 200
        assert client.get(f"/admin/flexcommerce_core/webhookdelivery/{d.pk}/change/").status_code == 200
        resp = client.post(
            "/admin/flexcommerce_core/webhookdelivery/",
            {"action": "retry_now", "_selected_action": [str(d.pk)]},
        )
        assert resp.status_code == 302

    def test_system_checks(self, fc, settings):
        from flexcommerce_core.checks import check_flexcommerce_settings

        settings.DEBUG = True
        fc(PRODUCT_MODELS=["tests.Product"])
        assert check_flexcommerce_settings(None) == []
        fc(PRODUCT_MODELS=[])
        assert [m.id for m in check_flexcommerce_settings(None)] == ["flexcommerce.W001"]
        fc(PRODUCT_MODELS=["nope.Model", "tests.IntPKModel"], ASYNC_EXECUTOR="celery", VAT_RATE=7.5)
        ids = {m.id for m in check_flexcommerce_settings(None)}
        assert {"flexcommerce.E001", "flexcommerce.E002", "flexcommerce.E005"} <= ids
        fc(PRODUCT_MODELS=["tests.Product"], ASYNC_EXECUTOR="no.such.executor")
        assert "flexcommerce.E004" in {m.id for m in check_flexcommerce_settings(None)}
        settings.DEBUG = False
        fc(PRODUCT_MODELS=["tests.Product"], ASYNC_EXECUTOR="sync")
        ids = {m.id for m in check_flexcommerce_settings(None)}
        assert {"flexcommerce.W002", "flexcommerce.W003"} <= ids

    def test_urls_only_mount_installed_apps(self):
        from flexcommerce_core import urls

        included = {getattr(p, "urlconf_name", None) for p in urls.urlpatterns}
        assert not any("flexcommerce_cart" in str(name) for name in included)
