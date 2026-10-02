"""In-app inbox backend: stores the message for the user (read via ``/notifications/inbox/``)."""

from . import BaseNotificationBackend


class InAppBackend(BaseNotificationBackend):
    channel = "in_app"

    def send(self, recipient, subject, body, **kwargs):
        from django.contrib.auth import get_user_model

        from ..models import InAppNotification

        user = get_user_model().objects.filter(pk=recipient).first()
        if user is None:
            return self.fail("Unknown user")
        note = InAppNotification.objects.create(
            user=user,
            event=kwargs.get("event", ""),
            title=subject[:255],
            body=body,
            data=kwargs.get("data") or {},
        )
        return self.ok(str(note.pk))
