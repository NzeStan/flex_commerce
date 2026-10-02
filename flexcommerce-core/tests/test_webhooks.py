import json
import socket
import time
from unittest import mock

import pytest

from flexcommerce_core import events, webhooks
from flexcommerce_core.models import WebhookDelivery, WebhookEndpoint

pytestmark = pytest.mark.django_db

PUBLIC_ADDR = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.fixture(autouse=True)
def public_dns():
    with mock.patch("socket.getaddrinfo", return_value=PUBLIC_ADDR):
        yield


@pytest.fixture
def endpoint():
    return WebhookEndpoint.objects.create(url="https://hooks.example.com/fc", event="order.created", secret="topsecret")


def fake_post(status=200, text="ok"):
    return mock.patch.object(webhooks, "_post", return_value=(status, text))


class TestSigning:
    def test_sign_and_verify(self):
        body = b'{"a":1}'
        ts = int(time.time())
        header = webhooks.sign_payload("s", body, ts)
        assert header.startswith(f"t={ts},v1=")
        assert webhooks.verify_signature("s", body, header)
        assert not webhooks.verify_signature("wrong", body, header)
        assert not webhooks.verify_signature("s", b"tampered", header)

    def test_verify_rejects_old_and_malformed(self):
        old = webhooks.sign_payload("s", b"x", int(time.time()) - 3600)
        assert not webhooks.verify_signature("s", b"x", old)
        assert webhooks.verify_signature("s", b"x", old, tolerance=0)
        for bad in ["", "garbage", "t=abc,v1=x", None]:
            assert not webhooks.verify_signature("s", b"x", bad)


class TestUrlValidation:
    def test_https_required(self):
        with pytest.raises(webhooks.UnsafeWebhookURL):
            webhooks.validate_webhook_url("http://example.com/hook")

    def test_http_allowed_when_configured(self, fc):
        fc(WEBHOOK_REQUIRE_HTTPS=False)
        webhooks.validate_webhook_url("http://example.com/hook")

    def test_no_host(self):
        with pytest.raises(webhooks.UnsafeWebhookURL):
            webhooks.validate_webhook_url("https:///nohost")

    @pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.0.5", "::1", "0.0.0.0"])  # noqa: S104
    def test_private_addresses_blocked(self, ip):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        with mock.patch("socket.getaddrinfo", return_value=[(family, 1, 6, "", (ip, 443))]):
            with pytest.raises(webhooks.UnsafeWebhookURL):
                webhooks.validate_webhook_url("https://internal.example.com/")

    def test_private_allowed_for_dev(self, fc):
        fc(WEBHOOK_ALLOW_PRIVATE_URLS=True)
        with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("127.0.0.1", 443))]):
            webhooks.validate_webhook_url("https://localhost/")

    def test_unresolvable(self):
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            with pytest.raises(webhooks.UnsafeWebhookURL):
                webhooks.validate_webhook_url("https://does-not-exist.invalid/")


class TestFanOutAndDelivery:
    def test_event_creates_signed_delivery(self, endpoint):
        WebhookEndpoint.objects.create(url="https://all.example.com/", event="*")
        WebhookEndpoint.objects.create(url="https://other.example.com/", event="order.paid")
        WebhookEndpoint.objects.create(url="https://off.example.com/", event="order.created", is_active=False)
        with fake_post() as post:
            events.emit("order.created", payload={"order_number": "FC1"})
        assert WebhookDelivery.objects.count() == 2
        assert post.call_count == 2
        call = next(c for c in post.call_args_list if c.args[0] == endpoint.url)
        url, body, headers, timeout = call.args
        envelope = json.loads(body)
        assert envelope["event"] == "order.created"
        assert envelope["data"] == {"order_number": "FC1"}
        assert headers["X-FlexCommerce-Event"] == "order.created"
        assert webhooks.verify_signature("topsecret", body, headers["X-FlexCommerce-Signature"])
        d = WebhookDelivery.objects.get(endpoint=endpoint)
        assert d.status == WebhookDelivery.STATUS_SUCCESS
        assert d.attempts == 1 and d.delivered_at and d.response_status == 200

    def test_no_endpoints_no_rows(self):
        assert webhooks.queue_event("order.created", {}) == []

    def test_failure_retries_with_backoff_then_fails(self, endpoint, fc):
        fc(WEBHOOK_MAX_ATTEMPTS=3)
        with fake_post(500, "err"):
            webhooks.queue_event("order.created", {"x": 1})
        d = WebhookDelivery.objects.get()
        assert d.status == WebhookDelivery.STATUS_PENDING
        assert d.attempts == 1 and d.last_error == "HTTP 500" and d.next_attempt_at
        endpoint.refresh_from_db()
        assert endpoint.failure_count == 1
        WebhookDelivery.objects.update(next_attempt_at=d.created_at)
        with fake_post(500):
            assert webhooks.deliver_due() == {"attempted": 1, "delivered": 0}
            WebhookDelivery.objects.update(next_attempt_at=d.created_at)
            webhooks.deliver_due()
        d.refresh_from_db()
        assert d.status == WebhookDelivery.STATUS_FAILED and d.attempts == 3 and d.next_attempt_at is None
        # already failed: deliver is a no-op
        assert webhooks.deliver(d.pk) is None

    def test_success_resets_failure_count(self, endpoint):
        endpoint.failure_count = 5
        endpoint.save()
        with fake_post(204):
            webhooks.queue_event("order.created", {})
        endpoint.refresh_from_db()
        assert endpoint.failure_count == 0 and endpoint.last_triggered_at

    def test_network_error(self, endpoint):
        with mock.patch.object(webhooks, "_post", side_effect=TimeoutError("slow")):
            webhooks.queue_event("order.created", {})
        d = WebhookDelivery.objects.get()
        assert "TimeoutError" in d.last_error and d.status == WebhookDelivery.STATUS_PENDING

    def test_unsafe_url_never_retried(self, fc):
        fc(WEBHOOK_MAX_ATTEMPTS=5)
        WebhookEndpoint.objects.create(url="http://plain.example.com/", event="order.created")
        with fake_post() as post:
            webhooks.queue_event("order.created", {})
        post.assert_not_called()
        d = WebhookDelivery.objects.get()
        assert d.status == WebhookDelivery.STATUS_FAILED and "Unsafe URL" in d.last_error

    def test_endpoint_auto_disabled(self, endpoint, fc):
        fc(WEBHOOK_DISABLE_AFTER_FAILURES=2)
        with fake_post(500):
            webhooks.queue_event("order.created", {})
            webhooks.queue_event("order.created", {})
        endpoint.refresh_from_db()
        assert endpoint.is_active is False

    def test_backoff_delay(self):
        assert webhooks.backoff_delay(1).total_seconds() == 60
        assert webhooks.backoff_delay(3).total_seconds() == 240
        assert webhooks.backoff_delay(50).total_seconds() == 360 * 60

    def test_post_does_not_follow_redirects(self):
        import urllib.error

        handler = webhooks._NoRedirect()
        assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil/") is None
        err = urllib.error.HTTPError("https://x", 302, "Found", {}, None)
        with mock.patch.object(webhooks._opener, "open", side_effect=err):
            assert webhooks._post("https://x", b"{}", {}, 1) == (302, "")

    def test_post_success_path(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"fine"
        with mock.patch.object(webhooks._opener, "open", return_value=response):
            assert webhooks._post("https://x", b"{}", {}, 1) == (200, "fine")
