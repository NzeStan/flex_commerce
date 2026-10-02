"""
Notification dispatcher (outbox pattern).

    notify("order.shipped", context, recipients=[Recipient(user=u)], dedupe="order:123")

1. For every recipient and enabled channel, checks the user's preferences,
   renders the message and writes a ``NotificationLog`` row (the outbox).
   A ``dedupe`` key makes the same event impossible to send twice.
2. After commit, each row is delivered through the async executor. Failures are
   retried with exponential backoff by the ``notifications.retry`` job.

The class API (``NotificationDispatcher.dispatch / dispatch_all / dispatch_for_user``) still works.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from flexcommerce_core import events
from flexcommerce_core.tasks import enqueue

from .conf import notifications_setting
from .models import (
    CHANNEL_EMAIL,
    CHANNEL_IN_APP,
    CHANNEL_PUSH,
    CHANNEL_SMS,
    DeviceToken,
    NotificationLog,
    NotificationPreference,
)
from .rendering import render_message

logger = logging.getLogger("flexcommerce.notifications")

BACKEND_SETTING_MAP = {
    CHANNEL_EMAIL: "NOTIFICATION_EMAIL_BACKEND",
    CHANNEL_SMS: "NOTIFICATION_SMS_BACKEND",
    CHANNEL_PUSH: "NOTIFICATION_PUSH_BACKEND",
    CHANNEL_IN_APP: "NOTIFICATION_IN_APP_BACKEND",
}
DEFAULT_BACKEND_MAP = {
    CHANNEL_EMAIL: "flexcommerce_notifications.backends.email.DjangoEmailBackend",
    CHANNEL_SMS: "flexcommerce_notifications.backends.NullBackend",
    CHANNEL_PUSH: "flexcommerce_notifications.backends.NullBackend",
    CHANNEL_IN_APP: "flexcommerce_notifications.backends.inapp.InAppBackend",
}


@dataclass
class Recipient:
    """Who to notify. Any missing address simply skips that channel."""

    user: object = None
    email: str = ""
    phone: str = ""
    name: str = ""
    extra: dict = field(default_factory=dict)

    def addresses(self):
        user = self.user
        email = self.email or (getattr(user, "email", "") if user else "")
        phone = self.phone or (
            str(getattr(user, "phone", "") or getattr(user, "phone_number", "") or "") if user else ""
        )
        result = {CHANNEL_EMAIL: [email] if email else [], CHANNEL_SMS: [phone] if phone else []}
        if user is not None and getattr(user, "pk", None):
            tokens = list(DeviceToken.objects.filter(user=user, is_active=True).values_list("token", flat=True))
            legacy = getattr(user, "push_token", None)
            result[CHANNEL_PUSH] = tokens or ([legacy] if legacy else [])
            result[CHANNEL_IN_APP] = [str(user.pk)]
        else:
            result[CHANNEL_PUSH] = []
            result[CHANNEL_IN_APP] = []
        return result


def _load_backend(channel):
    """Resolve and instantiate the backend for a channel."""
    from flexcommerce_core.conf import fc_setting

    path = fc_setting(BACKEND_SETTING_MAP.get(channel, ""), None) or DEFAULT_BACKEND_MAP.get(
        channel, "flexcommerce_notifications.backends.NullBackend"
    )
    try:
        return import_string(path)()
    except Exception as exc:
        logger.error("Failed to load notification backend '%s': %s", path, exc)
        from .backends import NullBackend

        return NullBackend()


def event_channels(event, requested=None):
    """Channels to use for ``event`` honouring NOTIFICATION_CHANNELS / NOTIFICATION_EVENTS."""
    enabled = list(notifications_setting("NOTIFICATION_CHANNELS"))
    override = (notifications_setting("NOTIFICATION_EVENTS") or {}).get(event, True)
    if override is False:
        return []
    channels = requested or enabled
    if isinstance(override, (list, tuple)):
        channels = [c for c in channels if c in override]
    return [c for c in channels if c in enabled]


def _json_safe(context):
    import json

    return json.loads(json.dumps(context, cls=DjangoJSONEncoder, default=str))


def base_context(context):
    frontend = (notifications_setting("FRONTEND_URL") or "").rstrip("/")
    ctx = {"store_name": notifications_setting("STORE_NAME"), "frontend_url": frontend, **context}
    if frontend and ctx.get("order_number") and "order_url" not in ctx:
        ctx["order_url"] = notifications_setting("ORDER_URL_TEMPLATE").format(frontend=frontend, **ctx)
    ctx.setdefault("order_url", "")
    return _json_safe(ctx)


def notify(event, context, recipients, channels=None, dedupe=None, data=None):
    """Queue notifications. Returns the created ``NotificationLog`` rows."""
    context = base_context(context)
    logs = []
    for recipient in recipients:
        user = recipient.user if getattr(recipient.user, "pk", None) else None
        prefs = NotificationPreference.objects.filter(user=user).first() if user else None
        addresses = recipient.addresses()
        for channel in event_channels(event, channels):
            for address in addresses.get(channel, []):
                key = f"{dedupe}:{channel}:{address}" if dedupe else None
                if prefs is not None and not prefs.allows(channel, event):
                    logs.append(
                        _create(
                            event,
                            channel,
                            address,
                            user,
                            context,
                            key,
                            status=NotificationLog.STATUS_SKIPPED,
                            body="Skipped: user preference",
                        )
                    )
                    continue
                rendered = render_message(
                    event,
                    channel,
                    {
                        **context,
                        "customer_name": recipient.name or context.get("customer_name", "Customer"),
                    },
                )
                logs.append(_create(event, channel, address, user, context, key, data=data, **rendered))
    logs = [log for log in logs if log is not None]
    for log in logs:
        if log.status == NotificationLog.STATUS_PENDING:
            events.on_commit(lambda pk=str(log.pk): enqueue("flexcommerce_notifications.dispatcher.deliver", pk))
    return logs


def _create(
    event,
    channel,
    recipient,
    user,
    context,
    dedupe_key,
    status=NotificationLog.STATUS_PENDING,
    subject="",
    body="",
    html_body="",
    data=None,
):
    try:
        with transaction.atomic():
            return NotificationLog.objects.create(
                event=event,
                channel=channel,
                recipient=str(recipient)[:255],
                user=user,
                subject=subject[:255],
                body=body,
                html_body=html_body,
                data=_json_safe(data or {}),
                status=status,
                context_snapshot=context,
                dedupe_key=dedupe_key[:255] if dedupe_key else None,
                next_attempt_at=timezone.now() if status == NotificationLog.STATUS_PENDING else None,
            )
    except IntegrityError:
        logger.debug("Duplicate notification suppressed: %s", dedupe_key)
        return None


def backoff(attempts):
    return timedelta(minutes=min(5 * 2 ** max(attempts - 1, 0), 24 * 60))


def deliver(log_id):
    """Send one queued notification. Safe to call concurrently / repeatedly."""
    with transaction.atomic():
        log = (
            NotificationLog.objects.select_for_update(skip_locked=True)
            .filter(pk=log_id, status=NotificationLog.STATUS_PENDING)
            .first()
        )
        if log is None:
            return None
        log.attempts += 1
        log.next_attempt_at = timezone.now() + backoff(log.attempts)
        log.save(update_fields=["attempts", "next_attempt_at", "updated_at"])

    backend = _load_backend(log.channel)
    log.provider = backend.__class__.__name__
    try:
        result = backend.send(
            log.recipient,
            log.subject,
            log.body,
            html_body=log.html_body,
            data=log.data,
            event=log.event,
        )
    except Exception as exc:  # a misbehaving backend must not crash the worker
        logger.exception("Notification backend %s raised", log.provider)
        result = {"success": False, "error": str(exc)}
    if result.get("success"):
        log.status = NotificationLog.STATUS_SENT
        log.provider_message_id = str(result.get("message_id") or "")[:255]
        log.sent_at = timezone.now()
        log.next_attempt_at = None
        log.error_message = ""
    else:
        log.error_message = str(result.get("error") or "")[:2000]
        if result.get("invalid_token") and log.channel == CHANNEL_PUSH:
            DeviceToken.objects.filter(token=log.recipient).update(is_active=False)
            log.attempts = notifications_setting("NOTIFICATION_MAX_ATTEMPTS")
        if log.attempts >= notifications_setting("NOTIFICATION_MAX_ATTEMPTS"):
            log.status = NotificationLog.STATUS_FAILED
            log.next_attempt_at = None
    log.save(
        update_fields=[
            "status",
            "provider",
            "provider_message_id",
            "sent_at",
            "error_message",
            "next_attempt_at",
            "attempts",
            "updated_at",
        ]
    )
    return log.status == NotificationLog.STATUS_SENT


def deliver_due(limit=500):
    """Job: retry pending notifications whose backoff has elapsed."""
    due = list(
        NotificationLog.objects.filter(status=NotificationLog.STATUS_PENDING, next_attempt_at__lte=timezone.now())
        .order_by("next_attempt_at")
        .values_list("pk", flat=True)[:limit]
    )
    sent = sum(1 for pk in due if deliver(pk))
    return {"attempted": len(due), "sent": sent}


# ── class API ────────────────────────────────────────────────────────────────────


class NotificationDispatcher:
    @classmethod
    def dispatch(cls, event, channel, recipient, context, user=None, async_send=True, **kwargs):
        if not recipient:
            return None
        rcpt = Recipient(user=user)
        if channel == CHANNEL_EMAIL:
            rcpt.email = recipient
        elif channel == CHANNEL_SMS:
            rcpt.phone = recipient
        prefs = NotificationPreference.objects.filter(user=user).first() if user else None
        ctx = base_context(context)
        if prefs is not None and not prefs.allows(channel, event):
            return _create(
                event,
                channel,
                recipient,
                user,
                ctx,
                None,
                status=NotificationLog.STATUS_SKIPPED,
                body="Skipped: user preference",
            )
        rendered = render_message(event, channel, ctx)
        log = _create(event, channel, recipient, user, ctx, None, data=kwargs.get("data"), **rendered)
        if async_send:
            events.on_commit(lambda: enqueue("flexcommerce_notifications.dispatcher.deliver", str(log.pk)))
        else:
            deliver(log.pk)
            log.refresh_from_db()
        return log

    @classmethod
    def dispatch_all(cls, event, recipient_map, context, user=None, **kwargs):
        return [
            log
            for channel, recipient in recipient_map.items()
            if recipient
            for log in [cls.dispatch(event, channel, recipient, context, user=user, **kwargs)]
            if log
        ]

    @classmethod
    def dispatch_for_user(cls, event, user, context, **kwargs):
        return notify(event, context, [Recipient(user=user)])
