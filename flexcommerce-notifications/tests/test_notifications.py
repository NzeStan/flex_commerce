from io import StringIO
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command

from flexcommerce_core.http import HTTPClientError
from flexcommerce_notifications import dispatcher
from flexcommerce_notifications.backends import ConsoleBackend, NullBackend
from flexcommerce_notifications.backends.email import DjangoEmailBackend, TermiiEmailBackend
from flexcommerce_notifications.backends.inapp import InAppBackend
from flexcommerce_notifications.backends.push import FCMPushBackend, OneSignalPushBackend
from flexcommerce_notifications.backends.sms import (
    AfricasTalkingSMSBackend,
    KudismsSMSBackend,
    TermiiSMSBackend,
    TwilioSMSBackend,
)
from flexcommerce_notifications.dispatcher import (
    NotificationDispatcher,
    Recipient,
    deliver,
    deliver_due,
    notify,
)
from flexcommerce_notifications.models import (
    DeviceToken,
    InAppNotification,
    NotificationLog,
    NotificationPreference,
    NotificationTemplate,
)
from flexcommerce_notifications.rendering import render_message
from flexcommerce_notifications.signal_handlers import notify_staff, staff_recipients

from .conftest import client_for

pytestmark = pytest.mark.django_db
ORDER_CTX = {
    "order_number": "FC123",
    "grand_total": "15,000.00",
    "currency": "NGN",
    "payment_method": "pay_on_delivery",
}


class TestRendering:
    def test_default_messages(self):
        email = render_message(
            "order.created",
            "email",
            {
                **ORDER_CTX,
                "customer_name": "Chidi",
                "store_name": "Naija Mart",
                "order_url": "https://x/o/FC123",
            },
        )
        assert email["subject"] == "Order FC123 received"
        assert "have the amount ready on delivery" in email["body"]
        assert "<html" in email["html_body"] and "View your order" in email["html_body"]
        sms = render_message("order.created", "sms", {**ORDER_CTX, "store_name": "Naija Mart"})
        assert sms["body"].startswith("Naija Mart: order FC123 received") and sms["html_body"] == ""

    def test_unknown_event_and_db_override(self):
        generic = render_message("custom.event", "push", {"body": "hello"})
        assert generic["subject"] == "Custom Event" and generic["body"] == "hello"
        NotificationTemplate.objects.create(
            event="order.created",
            channel="email",
            name="Custom",
            subject="Yay {{ order_number }}",
            body="Hi {{customer_name}}",
            html_body="<b>{{ customer_name }}</b>",
        )
        custom = render_message("order.created", "email", {"order_number": "FC9", "customer_name": "<script>"})
        assert custom["subject"] == "Yay FC9" and custom["body"] == "Hi <script>"
        assert custom["html_body"] == "<b>&lt;script&gt;</b>"  # HTML is escaped

    def test_sms_truncated(self):
        long = render_message("x", "sms", {"body": "a" * 2000})
        assert len(long["body"]) == 612


class TestDispatch:
    def test_notify_queues_and_delivers_all_channels(self, user):
        DeviceToken.objects.create(user=user, token="device-1")
        logs = notify(
            "order.created",
            ORDER_CTX,
            [Recipient(user=user, phone="08031234567")],
            dedupe="order:1",
        )
        assert {log.channel for log in logs} == {"email", "sms", "push", "in_app"}
        assert all(log.status == NotificationLog.STATUS_SENT for log in NotificationLog.objects.all())
        assert mail.outbox[0].to == ["chidi@example.com"] and mail.outbox[0].subject == "Order FC123 received"
        assert "https://shop.example.com/orders/FC123" in mail.outbox[0].body
        assert InAppNotification.objects.get(user=user).title == "Order FC123 received"

    def test_dedupe(self, user):
        notify("order.created", ORDER_CTX, [Recipient(user=user)], dedupe="order:1")
        again = notify("order.created", ORDER_CTX, [Recipient(user=user)], dedupe="order:1")
        assert again == [] and len(mail.outbox) == 1

    def test_guest_recipient(self):
        notify(
            "order.shipped",
            {**ORDER_CTX, "carrier": "GIG"},
            [Recipient(email="guest@example.com", name="Guest")],
        )
        assert NotificationLog.objects.get().channel == "email"
        assert "Hi Guest" in mail.outbox[0].body and "with GIG" in mail.outbox[0].body

    def test_preferences(self, user):
        NotificationPreference.objects.create(
            user=user,
            email_order_updates=False,
            email_marketing=False,
            email_abandoned_cart=False,
            in_app_marketing=False,
        )
        notify("order.created", ORDER_CTX, [Recipient(user=user)])
        notify("cart.abandoned", {"item_count": 2}, [Recipient(user=user)])
        statuses = dict(NotificationLog.objects.values_list("event", "status").filter(channel="email"))
        assert statuses == {"order.created": "skipped", "cart.abandoned": "skipped"}
        # order updates always reach the in-app inbox
        assert NotificationLog.objects.get(event="order.created", channel="in_app").status == "sent"
        assert NotificationLog.objects.get(event="cart.abandoned", channel="in_app").status == "skipped"

    def test_event_switches(self, user, fc):
        fc(
            NOTIFICATION_EVENTS={"cart.abandoned": False, "order.shipped": ["sms"]},
            NOTIFICATION_CHANNELS=["email", "sms", "in_app"],
        )
        assert notify("cart.abandoned", {}, [Recipient(user=user)]) == []
        logs = notify("order.shipped", ORDER_CTX, [Recipient(user=user, phone="08030000000")])
        assert [log.channel for log in logs] == ["sms"]
        assert dispatcher.event_channels("order.created", ["email", "push"]) == ["email"]

    def test_failure_backoff_and_retry(self, user, fc):
        fc(
            NOTIFICATION_EMAIL_BACKEND="tests.test_notifications.FlakyBackend",
            NOTIFICATION_MAX_ATTEMPTS=3,
        )
        FlakyBackend.calls = 0
        log = notify("order.created", ORDER_CTX, [Recipient(email="x@example.com")])[0]
        log.refresh_from_db()
        assert log.status == NotificationLog.STATUS_PENDING and log.attempts == 1 and "boom" in log.error_message
        assert log.next_attempt_at > log.created_at
        NotificationLog.objects.update(next_attempt_at=log.created_at)
        assert deliver_due() == {"attempted": 1, "sent": 0}
        NotificationLog.objects.update(next_attempt_at=log.created_at)
        deliver_due()
        log.refresh_from_db()
        assert log.status == NotificationLog.STATUS_FAILED and log.attempts == 3
        assert deliver(log.pk) is None  # nothing to do for failed rows

    def test_backend_exception_is_contained(self, fc):
        fc(NOTIFICATION_EMAIL_BACKEND="tests.test_notifications.RaisingBackend")
        log = notify("order.created", ORDER_CTX, [Recipient(email="x@example.com")])[0]
        log.refresh_from_db()
        assert "kaboom" in log.error_message

    def test_bad_backend_path_falls_back_to_null(self, fc):
        fc(NOTIFICATION_EMAIL_BACKEND="no.such.Backend")
        log = notify("order.created", ORDER_CTX, [Recipient(email="x@example.com")])[0]
        log.refresh_from_db()
        assert log.status == NotificationLog.STATUS_SENT and log.provider == "NullBackend"

    def test_invalid_push_token_is_disabled(self, user, fc):
        fc(
            NOTIFICATION_PUSH_BACKEND="tests.test_notifications.DeadTokenBackend",
            NOTIFICATION_CHANNELS=["push"],
        )
        DeviceToken.objects.create(user=user, token="stale")
        notify("order.created", ORDER_CTX, [Recipient(user=user)])
        assert DeviceToken.objects.get().is_active is False
        assert NotificationLog.objects.get().status == NotificationLog.STATUS_FAILED

    def test_legacy_user_attributes(self, fc):
        fc(NOTIFICATION_CHANNELS=["sms", "push"])
        legacy_user = SimpleNamespace(pk=None, email="", phone="08011112222", push_token="tok")
        addresses = Recipient(user=legacy_user).addresses()
        assert addresses["sms"] == ["08011112222"] and addresses["push"] == []

    def test_staff_alerts(self, staff, fc):
        assert [r.user for r in staff_recipients()] == [staff]
        notify_staff("inventory.low_stock", {"sku": "RICE", "available": 2, "on_hand": 2})
        assert NotificationLog.objects.filter(event="inventory.low_stock", channel="email").exists()
        assert notify_staff("not.a.staff.event", {}) == []
        fc(STAFF_NOTIFICATION_EMAILS=["ops@example.com"])
        assert [r.email for r in staff_recipients()] == ["ops@example.com"]


class FlakyBackend(NullBackend):
    calls = 0

    def send(self, recipient, subject, body, **kwargs):
        FlakyBackend.calls += 1
        return self.fail("boom")


class RaisingBackend(NullBackend):
    def send(self, *args, **kwargs):
        raise RuntimeError("kaboom")


class DeadTokenBackend(NullBackend):
    def send(self, *args, **kwargs):
        return {**self.fail("UNREGISTERED"), "invalid_token": True}


class TestLegacyAPI:
    def test_dispatch_variants(self, user):
        log = NotificationDispatcher.dispatch(
            "order.created", "email", "a@b.com", ORDER_CTX, user=user, async_send=False
        )
        assert log.status == NotificationLog.STATUS_SENT
        assert NotificationDispatcher.dispatch("order.created", "email", "", ORDER_CTX) is None
        logs = NotificationDispatcher.dispatch_all(
            "order.created", {"email": "c@d.com", "sms": "08030000000", "push": ""}, ORDER_CTX
        )
        assert {log.channel for log in logs} == {"email", "sms"}
        assert NotificationDispatcher.dispatch_for_user("order.created", user, ORDER_CTX)
        NotificationPreference.objects.create(user=user, email_order_updates=False)
        skipped = NotificationDispatcher.dispatch("order.created", "email", "a@b.com", ORDER_CTX, user=user)
        assert skipped.status == NotificationLog.STATUS_SKIPPED
        assert dispatcher._load_backend("email").__class__ is DjangoEmailBackend


def http(status, body):
    return mock.patch("flexcommerce_notifications.backends.sms.request_json", return_value=(status, body))


class TestProviders:
    def test_termii(self, fc):
        backend = TermiiSMSBackend()
        assert backend.send("0803", "", "x")["success"] is False  # not configured
        fc(TERMII_API_KEY="k", TERMII_SENDER_ID="Shop")
        with http(200, {"code": "ok", "message_id": "m1"}) as req:
            assert backend.send("08031234567", "", "hello") == {
                "success": True,
                "message_id": "m1",
                "error": None,
            }
        assert req.call_args.kwargs["payload"]["to"] == "2348031234567"
        with http(400, {"message": "Insufficient balance"}):
            assert backend.send("0803", "", "x")["error"] == "Insufficient balance"
        with mock.patch(
            "flexcommerce_notifications.backends.sms.request_json",
            side_effect=HTTPClientError("down"),
        ):
            assert backend.send("0803", "", "x")["success"] is False
        assert TermiiSMSBackend._normalize_phone("08031234567") == "2348031234567"

    def test_africastalking(self, fc):
        backend = AfricasTalkingSMSBackend()
        assert backend.send("0803", "", "x")["success"] is False
        fc(AT_USERNAME="sandbox", AT_API_KEY="k", AT_SENDER_ID="SHOP")
        ok = {"SMSMessageData": {"Recipients": [{"status": "Success", "messageId": "ATX"}]}}
        with http(201, ok) as req:
            assert backend.send("08031234567", "", "hi")["message_id"] == "ATX"
        assert "sandbox" in req.call_args.args[1] and req.call_args.kwargs["form"]["to"] == "+2348031234567"
        with http(201, {"SMSMessageData": {"Recipients": [{"status": "InvalidPhoneNumber"}]}}):
            assert backend.send("1", "", "x")["error"] == "InvalidPhoneNumber"
        with mock.patch(
            "flexcommerce_notifications.backends.sms.request_json",
            side_effect=HTTPClientError("down"),
        ):
            assert backend.send("0803", "", "x")["success"] is False

    def test_twilio(self, fc):
        backend = TwilioSMSBackend()
        assert backend.send("0803", "", "x")["success"] is False
        fc(TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="t", TWILIO_FROM="+15550001111")
        with http(201, {"sid": "SM1"}) as req:
            assert backend.send("08031234567", "", "hi")["message_id"] == "SM1"
        assert req.call_args.kwargs["auth"] == ("AC1", "t")
        with http(400, {"message": "bad number"}):
            assert backend.send("1", "", "x")["error"] == "bad number"
        with mock.patch(
            "flexcommerce_notifications.backends.sms.request_json",
            side_effect=HTTPClientError("down"),
        ):
            assert backend.send("0803", "", "x")["success"] is False

    def test_kudisms(self, fc):
        backend = KudismsSMSBackend()
        assert backend.send("0803", "", "x")["success"] is False
        fc(KUDISMS_TOKEN="tok")
        with http(200, {"status": "success", "data": "ref"}) as req:
            assert backend.send("08031234567", "", "hi")["success"] is True
        assert req.call_args.kwargs["form"]["token"] == "tok" and "tok" not in req.call_args.args[1]
        with http(200, {"status": "error", "msg": "low balance"}):
            assert backend.send("0803", "", "x")["error"] == "low balance"
        with mock.patch(
            "flexcommerce_notifications.backends.sms.request_json",
            side_effect=HTTPClientError("down"),
        ):
            assert backend.send("0803", "", "x")["success"] is False

    def test_onesignal(self, fc):
        backend = OneSignalPushBackend()
        assert backend.send("t", "s", "b")["success"] is False
        fc(ONESIGNAL_APP_ID="app", ONESIGNAL_API_KEY="key")
        target = "flexcommerce_notifications.backends.push.request_json"
        with mock.patch(target, return_value=(200, {"id": "n1"})) as req:
            assert backend.send("sub-1", "Hi", "Body", data={"order": "1"})["message_id"] == "n1"
        assert req.call_args.kwargs["headers"]["Authorization"] == "Key key"
        with mock.patch(target, return_value=(400, {"errors": ["invalid"]})):
            assert backend.send("t", "s", "b")["success"] is False
        with mock.patch(target, side_effect=HTTPClientError("down")):
            assert backend.send("t", "s", "b")["success"] is False

    def test_fcm_v1(self, fc):
        backend = FCMPushBackend()
        assert backend.send("t", "s", "b")["error"] == "FCM_PROJECT_ID is not configured"
        fc(FCM_PROJECT_ID="shop-app")
        target = "flexcommerce_notifications.backends.push.request_json"
        with mock.patch.object(FCMPushBackend, "_access_token", return_value="ya29.token"):
            with mock.patch(target, return_value=(200, {"name": "projects/shop-app/messages/1"})) as req:
                assert backend.send("device", "Title", "Body", data={"n": 1})["success"] is True
            assert req.call_args.args[1].endswith("/projects/shop-app/messages:send")
            assert req.call_args.kwargs["payload"]["message"]["data"] == {"n": "1"}
            with mock.patch(target, return_value=(404, {"error": {"status": "UNREGISTERED"}})):
                result = backend.send("device", "T", "B")
                assert result["invalid_token"] is True
            with mock.patch(target, side_effect=HTTPClientError("down")):
                assert backend.send("device", "T", "B")["success"] is False
        with mock.patch.object(FCMPushBackend, "_access_token", side_effect=ImportError):
            assert "google-auth" in backend.send("d", "T", "B")["error"]
        with mock.patch.object(FCMPushBackend, "_access_token", side_effect=ValueError("bad key")):
            assert "credentials" in backend.send("d", "T", "B")["error"]

    def test_email_backends(self, user):
        assert DjangoEmailBackend().send("a@b.com", "S", "B", html_body="<p>B</p>")["success"] is True
        assert mail.outbox[0].alternatives[0][1] == "text/html"
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            assert DjangoEmailBackend().send("a@b.com", "S", "B")["success"] is False
        with pytest.warns(DeprecationWarning):
            TermiiEmailBackend()

    def test_misc_backends(self, user, capsys):
        assert ConsoleBackend().send("x", "Subject", "Body")["success"] is True
        assert "Subject" in capsys.readouterr().out
        assert InAppBackend().send("999999", "t", "b")["success"] is False
        assert "NullBackend" in repr(NullBackend())


class TestAPI:
    def test_inbox(self, user):
        notify("order.created", ORDER_CTX, [Recipient(user=user)])
        c = client_for(user)
        assert c.get("/api/notifications/inbox/unread-count/").json() == {"unread": 1}
        items = c.get("/api/notifications/inbox/?unread=1").json()["results"]
        assert items[0]["is_read"] is False
        assert c.post(f"/api/notifications/inbox/{items[0]['id']}/read/").status_code == 204
        assert c.get("/api/notifications/inbox/unread-count/").json() == {"unread": 0}
        notify("order.shipped", ORDER_CTX, [Recipient(user=user)])
        assert c.post("/api/notifications/inbox/read-all/").json() == {"marked": 1}
        assert client_for().get("/api/notifications/inbox/").status_code in (401, 403)

    def test_preferences(self, user):
        c = client_for(user)
        assert c.get("/api/notifications/preferences/").json()["sms_order_updates"] is True
        assert c.patch("/api/notifications/preferences/", {"email_marketing": True}, format="json").json()[
            "email_marketing"
        ]

    def test_devices(self, user, staff):
        c = client_for(user)
        assert c.post("/api/notifications/devices/", {"token": "fcm-abc", "platform": "ios"}).status_code == 201
        client_for(staff).post("/api/notifications/devices/", {"token": "fcm-abc"})  # token moves to new owner
        assert DeviceToken.objects.get().user == staff
        assert c.get("/api/notifications/devices/").json() == []
        assert client_for(staff).delete("/api/notifications/devices/fcm-abc/").status_code == 204

    def test_my_notifications_and_staff_logs(self, user, staff):
        notify("order.created", ORDER_CTX, [Recipient(user=user)])
        assert client_for(user).get("/api/notifications/logs/my-notifications/").json()["count"] == 2
        assert client_for(user).get("/api/notifications/logs/").status_code == 403
        s = client_for(staff)
        logs = s.get("/api/notifications/logs/?channel=email&status=sent&event=order.created").json()
        assert logs["count"] == 1
        retried = s.post(f"/api/notifications/logs/{logs['results'][0]['id']}/retry/").json()
        assert retried["status"] == "sent"

    def test_templates(self, staff):
        s = client_for(staff)
        bad = s.post(
            "/api/notifications/templates/",
            {"event": "order.created", "channel": "sms", "name": "x", "body": "{% if %}"},
            format="json",
        )
        assert bad.status_code == 400
        tpl = s.post(
            "/api/notifications/templates/",
            {
                "event": "order.created",
                "channel": "sms",
                "name": "SMS",
                "body": "Order {{ order_number }}",
            },
            format="json",
        ).json()
        preview = s.post(
            f"/api/notifications/templates/{tpl['id']}/preview/",
            {"context": {"order_number": "FC1"}},
            format="json",
        ).json()
        assert preview["body"] == "Order FC1"


class TestInfra:
    def test_command(self, fc):
        fc(
            NOTIFICATION_EMAIL_BACKEND="tests.test_notifications.FlakyBackend",
            NOTIFICATION_MAX_ATTEMPTS=1,
        )
        notify("order.created", ORDER_CTX, [Recipient(email="x@example.com")])
        out = StringIO()
        call_command("retry_failed_notifications", "--dry-run", stdout=out)
        assert "Would retry 1" in out.getvalue()
        call_command("retry_failed_notifications", "--channel=email", stdout=out)
        assert "Retried 1 notifications, 0 sent" in out.getvalue()

    def test_migrations_and_admin(self, client, user):
        from django.contrib.auth import get_user_model

        call_command(
            "makemigrations",
            "flexcommerce_notifications",
            "--check",
            "--dry-run",
            stdout=StringIO(),
        )
        notify("order.created", ORDER_CTX, [Recipient(user=user)])
        DeviceToken.objects.create(user=user, token="t1")
        NotificationPreference.objects.create(user=user)
        client.force_login(get_user_model().objects.create_superuser("root", "r@x.com", "x"))
        for m in (
            "notificationtemplate",
            "notificationlog",
            "notificationpreference",
            "inappnotification",
            "devicetoken",
        ):
            assert client.get(f"/admin/flexcommerce_notifications/{m}/").status_code == 200
        log = NotificationLog.objects.first()
        client.post(
            "/admin/flexcommerce_notifications/notificationlog/",
            {"action": "retry", "_selected_action": [str(log.pk)]},
        )
        assert "→" in str(log) and str(DeviceToken.objects.get()).startswith("android:")
        assert str(NotificationPreference.objects.get()).startswith("NotifPrefs(")
