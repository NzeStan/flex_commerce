"""
Push backends.

* ``FCMPushBackend`` — Firebase Cloud Messaging **HTTP v1** API. (The old
  ``fcm/send`` API was shut down by Google in 2024.) Needs
  ``pip install flexcommerce-notifications[fcm]`` and settings
  ``FCM_PROJECT_ID`` + ``FCM_SERVICE_ACCOUNT_FILE`` (path) or
  ``FCM_SERVICE_ACCOUNT_INFO`` (dict).
* ``OneSignalPushBackend`` — ``ONESIGNAL_APP_ID`` + ``ONESIGNAL_API_KEY``.
"""

import logging
import threading

from flexcommerce_core.conf import fc_setting
from flexcommerce_core.http import HTTPClientError, request_json

from . import BaseNotificationBackend

logger = logging.getLogger("flexcommerce.notifications.push")

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class FCMPushBackend(BaseNotificationBackend):
    channel = "push"
    _credentials = None
    _lock = threading.Lock()

    @classmethod
    def _access_token(cls):
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        with cls._lock:
            if cls._credentials is None:
                info = fc_setting("FCM_SERVICE_ACCOUNT_INFO", None)
                path = fc_setting("FCM_SERVICE_ACCOUNT_FILE", "")
                if info:
                    cls._credentials = service_account.Credentials.from_service_account_info(info, scopes=[FCM_SCOPE])
                else:
                    cls._credentials = service_account.Credentials.from_service_account_file(path, scopes=[FCM_SCOPE])
            if not cls._credentials.valid:
                cls._credentials.refresh(Request())
            return cls._credentials.token

    def send(self, recipient, subject, body, **kwargs):
        project = fc_setting("FCM_PROJECT_ID", "")
        if not project:
            return self.fail("FCM_PROJECT_ID is not configured")
        try:
            token = self._access_token()
        except ImportError:
            return self.fail("google-auth is not installed (pip install flexcommerce-notifications[fcm])")
        except Exception as exc:  # bad credentials
            return self.fail(f"FCM credentials error: {exc}")
        data = {str(k): str(v) for k, v in (kwargs.get("data") or {}).items()}
        message = {
            "message": {
                "token": recipient,
                "notification": {"title": subject, "body": body},
                "data": data,
            }
        }
        try:
            status, response = request_json(
                "POST",
                f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
                headers={"Authorization": f"Bearer {token}"},
                payload=message,
                timeout=15,
            )
        except HTTPClientError as exc:
            return self.fail(exc)
        if status == 200 and response.get("name"):
            return self.ok(response["name"])
        error = (response.get("error") or {}).get("status") or response
        return {
            **self.fail(error),
            "invalid_token": error in ("NOT_FOUND", "UNREGISTERED", "INVALID_ARGUMENT"),
        }


FCMv1PushBackend = FCMPushBackend


class OneSignalPushBackend(BaseNotificationBackend):
    channel = "push"

    def send(self, recipient, subject, body, **kwargs):
        app_id, api_key = fc_setting("ONESIGNAL_APP_ID", ""), fc_setting("ONESIGNAL_API_KEY", "")
        if not app_id or not api_key:
            return self.fail("OneSignal credentials are not configured")
        payload = {
            "app_id": app_id,
            "include_subscription_ids": [recipient],
            "headings": {"en": subject},
            "contents": {"en": body},
            "data": kwargs.get("data") or {},
        }
        try:
            status, data = request_json(
                "POST",
                "https://api.onesignal.com/notifications",
                headers={"Authorization": f"Key {api_key}"},
                payload=payload,
                timeout=15,
            )
        except HTTPClientError as exc:
            return self.fail(exc)
        if status == 200 and data.get("id") and not data.get("errors"):
            return self.ok(data["id"])
        return self.fail(data.get("errors") or data)
