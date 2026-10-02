"""Re-queue failed notifications (they are delivered again with a fresh attempt budget)."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from flexcommerce_notifications.dispatcher import deliver
from flexcommerce_notifications.models import NotificationLog


class Command(BaseCommand):
    help = "Retry failed notifications."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--channel", default=None, help="email | sms | push | in_app")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        qs = NotificationLog.objects.filter(status=NotificationLog.STATUS_FAILED)
        if options["channel"]:
            qs = qs.filter(channel=options["channel"])
        ids = list(qs.order_by("created_at").values_list("pk", flat=True)[: options["limit"]])
        if options["dry_run"]:
            self.stdout.write(f"[DRY RUN] Would retry {len(ids)} failed notifications.")
            return
        NotificationLog.objects.filter(pk__in=ids).update(
            status=NotificationLog.STATUS_PENDING, attempts=0, next_attempt_at=timezone.now()
        )
        sent = sum(1 for pk in ids if deliver(pk))
        self.stdout.write(self.style.SUCCESS(f"Retried {len(ids)} notifications, {sent} sent."))
