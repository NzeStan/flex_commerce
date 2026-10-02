"""Initial migration for flexcommerce_core."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="created at"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                (
                    "extra_data",
                    models.JSONField(blank=True, default=dict, verbose_name="extra data"),
                ),
                ("object_id", models.UUIDField(db_index=True, verbose_name="object ID")),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("create", "Create"),
                            ("update", "Update"),
                            ("delete", "Delete"),
                            ("transition", "Transition"),
                        ],
                        max_length=20,
                        verbose_name="action",
                    ),
                ),
                ("actor", models.CharField(blank=True, max_length=255, verbose_name="actor")),
                ("changes", models.JSONField(blank=True, default=dict, verbose_name="changes")),
                (
                    "ip_address",
                    models.GenericIPAddressField(blank=True, null=True, verbose_name="IP address"),
                ),
                ("note", models.TextField(blank=True, verbose_name="note")),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="audit_logs",
                        to="contenttypes.contenttype",
                        verbose_name="content type",
                    ),
                ),
            ],
            options={
                "verbose_name": "audit log",
                "verbose_name_plural": "audit logs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="WebhookEndpoint",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="created at"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                (
                    "extra_data",
                    models.JSONField(blank=True, default=dict, verbose_name="extra data"),
                ),
                ("url", models.URLField(verbose_name="URL")),
                (
                    "event",
                    models.CharField(
                        choices=[
                            ("order.created", "Order Created"),
                            ("order.paid", "Order Paid"),
                            ("order.shipped", "Order Shipped"),
                            ("order.delivered", "Order Delivered"),
                            ("order.cancelled", "Order Cancelled"),
                            ("order.refunded", "Order Refunded"),
                            ("cart.abandoned", "Cart Abandoned"),
                            ("payment.success", "Payment Success"),
                            ("payment.failed", "Payment Failed"),
                        ],
                        max_length=50,
                        verbose_name="event",
                    ),
                ),
                ("secret", models.CharField(blank=True, max_length=255, verbose_name="secret")),
                ("is_active", models.BooleanField(default=True, verbose_name="is active")),
                (
                    "last_triggered_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="last triggered at"),
                ),
                (
                    "failure_count",
                    models.PositiveIntegerField(default=0, verbose_name="failure count"),
                ),
            ],
            options={
                "verbose_name": "webhook endpoint",
                "verbose_name_plural": "webhook endpoints",
            },
        ),
        migrations.CreateModel(
            name="Address",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="created at"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="updated at")),
                (
                    "extra_data",
                    models.JSONField(blank=True, default=dict, verbose_name="extra data"),
                ),
                (
                    "address_type",
                    models.CharField(
                        choices=[("billing", "Billing"), ("shipping", "Shipping")],
                        default="shipping",
                        max_length=20,
                        verbose_name="type",
                    ),
                ),
                ("first_name", models.CharField(max_length=100, verbose_name="first name")),
                ("last_name", models.CharField(max_length=100, verbose_name="last name")),
                ("email", models.EmailField(blank=True, verbose_name="email")),
                ("phone", models.CharField(blank=True, max_length=20, verbose_name="phone")),
                ("line1", models.CharField(max_length=255, verbose_name="address line 1")),
                (
                    "line2",
                    models.CharField(blank=True, max_length=255, verbose_name="address line 2"),
                ),
                ("city", models.CharField(max_length=100, verbose_name="city")),
                ("state", models.CharField(max_length=100, verbose_name="state")),
                (
                    "country",
                    models.CharField(default="Nigeria", max_length=100, verbose_name="country"),
                ),
                (
                    "postal_code",
                    models.CharField(blank=True, max_length=20, verbose_name="postal code"),
                ),
                ("is_default", models.BooleanField(default=False, verbose_name="is default")),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="addresses",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="user",
                    ),
                ),
            ],
            options={"verbose_name": "address", "verbose_name_plural": "addresses"},
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["content_type", "object_id"], name="core_audit_ct_idx"),
        ),
        migrations.AddIndex(
            model_name="address",
            index=models.Index(fields=["user", "address_type"], name="core_addr_user_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="webhookendpoint",
            unique_together={("url", "event")},
        ),
    ]
