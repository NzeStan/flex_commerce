"""
Notifications API.

Customers:
  GET   /notifications/inbox/                 in-app notifications (?unread=1)
  GET   /notifications/inbox/unread-count/
  POST  /notifications/inbox/{id}/read/       POST /notifications/inbox/read-all/
  GET|PATCH /notifications/preferences/
  POST  /notifications/devices/  {token, platform}     DELETE /notifications/devices/{token}/
  GET   /notifications/logs/my-notifications/
Staff:
  /notifications/templates/ CRUD  (+ POST {id}/preview/ {context})
  GET /notifications/logs/  (+ POST {id}/retry/)
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from flexcommerce_core.api import FlexCommerceAPIMixin, IsStaff, paginate
from flexcommerce_core.tasks import enqueue

from .models import (
    DeviceToken,
    InAppNotification,
    NotificationLog,
    NotificationPreference,
    NotificationTemplate,
)
from .serializers import (
    DeviceTokenSerializer,
    InAppNotificationSerializer,
    MyNotificationSerializer,
    NotificationLogSerializer,
    NotificationPreferenceSerializer,
    NotificationTemplateSerializer,
    TemplatePreviewSerializer,
)


class NotificationTemplateViewSet(FlexCommerceAPIMixin, viewsets.ModelViewSet):
    queryset = NotificationTemplate.objects.all().order_by("event", "channel")
    serializer_class = NotificationTemplateSerializer
    permission_classes = [IsStaff]

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        ser = TemplatePreviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(self.get_object().render(ser.validated_data["context"]))


class NotificationLogViewSet(FlexCommerceAPIMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationLogSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = NotificationLog.objects.order_by("-created_at")
        for field in ("status", "channel", "event"):
            if self.request.query_params.get(field):
                qs = qs.filter(**{field: self.request.query_params[field]})
        return qs

    @action(
        detail=False,
        methods=["get"],
        url_path="my-notifications",
        permission_classes=[permissions.IsAuthenticated],
    )
    def my_notifications(self, request):
        qs = NotificationLog.objects.filter(user=request.user, status=NotificationLog.STATUS_SENT).order_by(
            "-created_at"
        )
        return paginate(self, qs, MyNotificationSerializer)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        log = self.get_object()
        NotificationLog.objects.filter(pk=log.pk).update(
            status=NotificationLog.STATUS_PENDING, attempts=0, next_attempt_at=timezone.now()
        )
        enqueue("flexcommerce_notifications.dispatcher.deliver", str(log.pk))
        log.refresh_from_db()
        return Response(NotificationLogSerializer(log).data)


class InboxViewSet(FlexCommerceAPIMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = InAppNotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = InAppNotification.objects.filter(user=self.request.user).order_by("-created_at")
        if self.request.query_params.get("unread") in ("1", "true"):
            qs = qs.filter(read_at__isnull=True)
        return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": InAppNotification.objects.filter(user=request.user, read_at__isnull=True).count()})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        InAppNotification.objects.filter(user=request.user, pk=pk, read_at__isnull=True).update(read_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        count = InAppNotification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        return Response({"marked": count})


class NotificationPreferenceView(FlexCommerceAPIMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return Response(NotificationPreferenceSerializer(prefs).data)

    def patch(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        ser = NotificationPreferenceSerializer(prefs, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class DeviceTokenViewSet(
    FlexCommerceAPIMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = DeviceTokenSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "token"
    lookup_value_regex = "[^/]+"
    pagination_class = None

    def get_queryset(self):
        return DeviceToken.objects.filter(user=self.request.user)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        token, _ = DeviceToken.objects.update_or_create(
            token=ser.validated_data["token"],
            defaults={
                "user": request.user,
                "platform": ser.validated_data.get("platform", "android"),
                "is_active": True,
                "last_used_at": timezone.now(),
            },
        )
        return Response(DeviceTokenSerializer(token).data, status=status.HTTP_201_CREATED)
