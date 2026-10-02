import uuid

import pytest
from django.contrib.contenttypes.models import ContentType

from flexcommerce_core.models import Address, AuditLog, WebhookEndpoint
from flexcommerce_core.signals import audit_logged
from tests.models import SoftItem

pytestmark = pytest.mark.django_db


def make_address(**kwargs):
    data = {
        "first_name": "Ifeanyi",
        "last_name": "Nnamani",
        "line1": "12 GRA",
        "city": "Enugu",
        "state": "Enugu",
    }
    data.update(kwargs)
    return Address.objects.create(**data)


class TestTimeStampedUUIDModel:
    def test_uuid_pk_and_timestamps(self):
        a = make_address()
        assert isinstance(a.pk, uuid.UUID)
        assert a.created_at and a.updated_at
        assert a.extra_data == {}
        assert "Address" in repr(a) and str(a.pk) in repr(a)

    def test_extra_data_roundtrip(self):
        a = make_address(extra_data={"k": [1, 2]})
        a.refresh_from_db()
        assert a.extra_data == {"k": [1, 2]}


class TestSoftDelete:
    def test_delete_hides_row(self):
        item = SoftItem.objects.create()
        item.delete()
        assert item.is_deleted
        assert not SoftItem.objects.filter(pk=item.pk).exists()
        assert SoftItem.objects.all_with_deleted().filter(pk=item.pk).exists()
        assert SoftItem.objects.deleted_only().filter(pk=item.pk).exists()
        assert SoftItem.all_objects.filter(pk=item.pk).exists()

    def test_restore(self):
        item = SoftItem.objects.create()
        item.delete()
        item.restore()
        assert SoftItem.objects.filter(pk=item.pk).exists()
        assert not item.is_deleted

    def test_queryset_delete_is_soft(self):
        SoftItem.objects.create()
        SoftItem.objects.create()
        SoftItem.objects.all().delete()
        assert SoftItem.objects.count() == 0
        assert SoftItem.all_objects.count() == 2

    def test_hard_delete(self):
        item = SoftItem.objects.create()
        item.hard_delete()
        assert SoftItem.all_objects.count() == 0
        SoftItem.objects.create()
        SoftItem.objects.all_with_deleted().hard_delete()
        assert SoftItem.all_objects.count() == 0

    def test_soft_delete_can_be_disabled(self, fc):
        fc(SOFT_DELETE=False)
        item = SoftItem.objects.create()
        item.delete()
        assert SoftItem.all_objects.count() == 0


class TestAuditLog:
    def test_log_records_entry_and_signal(self, fc):
        fc(AUDIT_ENABLED=True)
        received = []
        audit_logged.connect(lambda sender, **kw: received.append(kw), weak=False, dispatch_uid="t1")
        try:
            a = make_address()
            entry = AuditLog.log(a, AuditLog.ACTION_UPDATE, actor="admin", changes={"x": 1}, ip="10.0.0.1", note="n")
        finally:
            audit_logged.disconnect(dispatch_uid="t1")
        assert entry.content_type == ContentType.objects.get_for_model(Address)
        assert entry.object_id == a.pk
        assert entry.changes == {"x": 1}
        assert entry.ip_address == "10.0.0.1"
        assert str(entry).startswith("update on")
        assert received and received[0]["entry"] == entry

    def test_disabled(self, fc):
        fc(AUDIT_ENABLED=False)
        assert AuditLog.log(make_address(), AuditLog.ACTION_CREATE) is None
        assert AuditLog.objects.count() == 0

    def test_actor_truncated(self):
        entry = AuditLog.log(make_address(), AuditLog.ACTION_CREATE, actor="x" * 400)
        assert len(entry.actor) == 255


class TestWebhookEndpoint:
    def test_secret_generated(self):
        ep = WebhookEndpoint.objects.create(url="https://example.com/hook", event="order.created")
        assert len(ep.secret) == 64
        assert "order.created" in str(ep)

    def test_explicit_secret_kept(self):
        ep = WebhookEndpoint.objects.create(url="https://e.com/h", event="*", secret="s3cret")
        assert ep.secret == "s3cret"


class TestAddress:
    def test_full_name_and_snapshot(self, user):
        a = make_address(user=user, landmark="Opposite Shoprite", lga="Enugu North")
        assert a.full_name == "Ifeanyi Nnamani"
        snap = a.to_snapshot()
        assert snap["landmark"] == "Opposite Shoprite"
        assert snap["lga"] == "Enugu North"
        assert "id" not in snap and "user" not in snap
        assert "Enugu" in str(a)

    def test_deleting_user_deletes_addresses(self, user):
        make_address(user=user)
        user.delete()
        assert Address.objects.count() == 0
