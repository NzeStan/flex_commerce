from rest_framework import serializers

from .models import (
    DeviceToken,
    InAppNotification,
    NotificationLog,
    NotificationPreference,
    NotificationTemplate,
)


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = [
            "id",
            "event",
            "channel",
            "name",
            "subject",
            "body",
            "html_body",
            "is_active",
            "created_at",
        ]

    def validate(self, attrs):
        from django.template import TemplateSyntaxError

        from .rendering import render_string

        for field in ("subject", "body", "html_body"):
            try:
                render_string(attrs.get(field, ""), {})
            except TemplateSyntaxError as exc:
                raise serializers.ValidationError({field: f"Template error: {exc}"}) from exc
        return attrs


class TemplatePreviewSerializer(serializers.Serializer):
    context = serializers.DictField(required=False, default=dict)


class NotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = [
            "id",
            "event",
            "channel",
            "recipient",
            "subject",
            "status",
            "attempts",
            "provider",
            "provider_message_id",
            "error_message",
            "sent_at",
            "created_at",
        ]
        read_only_fields = fields


class MyNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = ["id", "event", "channel", "subject", "body", "status", "sent_at", "created_at"]
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            "id",
            "email_order_updates",
            "email_marketing",
            "email_abandoned_cart",
            "sms_order_updates",
            "sms_marketing",
            "push_order_updates",
            "push_marketing",
            "in_app_marketing",
        ]
        read_only_fields = ["id"]


class InAppNotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = InAppNotification
        fields = ["id", "event", "title", "body", "data", "is_read", "read_at", "created_at"]
        read_only_fields = fields

    def get_is_read(self, obj):
        return obj.read_at is not None


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ["token", "platform", "is_active", "created_at"]
        read_only_fields = ["is_active", "created_at"]
        extra_kwargs = {"token": {"validators": []}}
