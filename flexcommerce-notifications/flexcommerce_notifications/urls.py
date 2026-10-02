from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    DeviceTokenViewSet,
    InboxViewSet,
    NotificationLogViewSet,
    NotificationPreferenceView,
    NotificationTemplateViewSet,
)

router = DefaultRouter()
router.include_root_view = False
router.register("notifications/templates", NotificationTemplateViewSet, basename="notification-template")
router.register("notifications/logs", NotificationLogViewSet, basename="notification-log")
router.register("notifications/inbox", InboxViewSet, basename="notification-inbox")
router.register("notifications/devices", DeviceTokenViewSet, basename="notification-device")

urlpatterns = [
    path(
        "notifications/preferences/",
        NotificationPreferenceView.as_view(),
        name="notification-preferences",
    ),
] + router.urls
