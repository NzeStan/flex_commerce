"""
Outgoing webhooks.

Every FlexCommerce domain event (``flexcommerce_core.signals.event``) is fanned
out to the active ``WebhookEndpoint`` rows subscribed to it. Each delivery is an
outbox row (``WebhookDelivery``) retried with exponential backoff.

Receivers verify authenticity like this::

    t, sig = parse(request.headers["X-FlexCommerce-Signature"])  # "t=...,v1=..."
    expected = hmac.new(secret, f"{t}.".encode() + raw_body, sha256).hexdigest()
    assert hmac.compare_digest(expected, sig) and abs(time.time() - int(t)) < 300

Security: URLs resolving to private, loopback, link-local or reserved addresses
are refused (SSRF protection) and redirects are never followed.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlparse

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .conf import fc_setting

logger = logging.getLogger("flexcommerce.webhooks")

SIGNATURE_HEADER = "X-FlexCommerce-Signature"
USER_AGENT = "FlexCommerce-Webhooks/2"


class UnsafeWebhookURLError(ValueError):
    pass


UnsafeWebhookURL = UnsafeWebhookURLError  # short alias


def sign_payload(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify_signature(secret: str, body: bytes, header: str, tolerance: int = 300) -> bool:
    """Helper for *receivers* of FlexCommerce webhooks."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        timestamp = int(parts["t"])
        signature = parts["v1"]
    except (KeyError, ValueError, AttributeError):
        return False
    if tolerance and abs(time.time() - timestamp) > tolerance:
        return False
    expected = sign_payload(secret, body, timestamp).split("v1=", 1)[1]
    return hmac.compare_digest(expected, signature)


def validate_webhook_url(url: str) -> None:
    """Raise ``UnsafeWebhookURLError`` if ``url`` is not safe to call from the server."""
    parsed = urlparse(url)
    allowed_schemes = {"https"} if fc_setting("WEBHOOK_REQUIRE_HTTPS", True) else {"http", "https"}
    if parsed.scheme not in allowed_schemes:
        raise UnsafeWebhookURLError(f"Scheme '{parsed.scheme}' is not allowed.")
    if not parsed.hostname:
        raise UnsafeWebhookURLError("URL has no host.")
    if fc_setting("WEBHOOK_ALLOW_PRIVATE_URLS", False):
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeWebhookURLError(f"Cannot resolve host: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeWebhookURLError(f"Host resolves to a non-public address ({ip}).")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _post(url, body: bytes, headers: dict, timeout: int):
    """POST and return ``(status_code, response_text)``. Isolated for tests."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - validated
    try:
        with _opener.open(request, timeout=timeout) as response:
            return response.status, response.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace") if exc.fp else ""


def backoff_delay(attempts: int) -> timedelta:
    """1m, 2m, 4m ... capped at 6h."""
    return timedelta(minutes=min(2 ** max(attempts - 1, 0), 360))


# ── Fan-out ──────────────────────────────────────────────────────────────────


def queue_event(name: str, payload: dict):
    """Create delivery rows for every endpoint subscribed to ``name``."""
    from .models import WebhookDelivery, WebhookEndpoint
    from .tasks import enqueue

    endpoints = WebhookEndpoint.objects.filter(is_active=True).filter(Q(event=name) | Q(event="*"))
    deliveries = [
        WebhookDelivery(
            endpoint=endpoint,
            event=name,
            payload=json.loads(json.dumps(payload, cls=DjangoJSONEncoder)),
            next_attempt_at=timezone.now(),
        )
        for endpoint in endpoints
    ]
    if not deliveries:
        return []
    WebhookDelivery.objects.bulk_create(deliveries)
    for delivery in deliveries:
        enqueue("flexcommerce_core.webhooks.deliver", str(delivery.pk))
    return deliveries


def on_domain_event(sender, name, payload, **kwargs):
    queue_event(name, payload)


# ── Delivery ─────────────────────────────────────────────────────────────────


def deliver(delivery_id):
    """Attempt one delivery. Safe to call concurrently and repeatedly."""
    from .models import WebhookDelivery, WebhookEndpoint
    from .signals import webhook_triggered

    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update(skip_locked=True)
            .select_related("endpoint")
            .filter(pk=delivery_id, status=WebhookDelivery.STATUS_PENDING)
            .first()
        )
        if delivery is None:
            return None
        # Claim it: push next attempt out so parallel workers skip it.
        delivery.attempts += 1
        delivery.next_attempt_at = timezone.now() + backoff_delay(delivery.attempts)
        delivery.save(update_fields=["attempts", "next_attempt_at", "updated_at"])

    endpoint = delivery.endpoint
    envelope = {
        "id": str(delivery.pk),
        "event": delivery.event,
        "created_at": delivery.created_at.isoformat(),
        "data": delivery.payload,
    }
    body = json.dumps(envelope, cls=DjangoJSONEncoder, separators=(",", ":")).encode()
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-FlexCommerce-Event": delivery.event,
        "X-FlexCommerce-Delivery": str(delivery.pk),
        SIGNATURE_HEADER: sign_payload(endpoint.secret, body, timestamp),
    }

    success = False
    try:
        validate_webhook_url(endpoint.url)
        status, text = _post(endpoint.url, body, headers, fc_setting("WEBHOOK_TIMEOUT", 10))
        delivery.response_status = status
        delivery.response_body = text[:2000]
        success = 200 <= status < 300
        delivery.last_error = "" if success else f"HTTP {status}"
    except UnsafeWebhookURLError as exc:
        delivery.last_error = f"Unsafe URL: {exc}"
        delivery.attempts = fc_setting("WEBHOOK_MAX_ATTEMPTS", 8)  # never retry
    except Exception as exc:  # network errors, timeouts
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:2000]

    now = timezone.now()
    if success:
        delivery.status = WebhookDelivery.STATUS_SUCCESS
        delivery.delivered_at = now
        delivery.next_attempt_at = None
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(failure_count=0, last_triggered_at=now)
    else:
        if delivery.attempts >= fc_setting("WEBHOOK_MAX_ATTEMPTS", 8):
            delivery.status = WebhookDelivery.STATUS_FAILED
            delivery.next_attempt_at = None
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(
            failure_count=F("failure_count") + 1, last_triggered_at=now
        )
        limit = fc_setting("WEBHOOK_DISABLE_AFTER_FAILURES", 50)
        if limit:
            WebhookEndpoint.objects.filter(pk=endpoint.pk, failure_count__gte=limit).update(is_active=False)
        logger.warning("Webhook delivery %s to %s failed: %s", delivery.pk, endpoint.url, delivery.last_error)

    delivery.save(
        update_fields=[
            "status",
            "attempts",
            "next_attempt_at",
            "response_status",
            "response_body",
            "last_error",
            "delivered_at",
            "updated_at",
        ]
    )
    webhook_triggered.send(sender=WebhookDelivery, delivery=delivery, success=success)
    return success


def deliver_due(limit=200):
    """Job: retry pending deliveries whose ``next_attempt_at`` has passed."""
    from .models import WebhookDelivery

    due = list(
        WebhookDelivery.objects.filter(status=WebhookDelivery.STATUS_PENDING, next_attempt_at__lte=timezone.now())
        .order_by("next_attempt_at")
        .values_list("pk", flat=True)[:limit]
    )
    delivered = sum(1 for pk in due if deliver(pk))
    return {"attempted": len(due), "delivered": delivered}
