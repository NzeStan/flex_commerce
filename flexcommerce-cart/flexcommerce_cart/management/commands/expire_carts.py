"""Expire stale anonymous carts and emit abandonment events (also run by flexcommerce_run_jobs)."""

from django.core.management.base import BaseCommand

from flexcommerce_cart.services import expire_carts, notify_abandoned_carts


class Command(BaseCommand):
    help = "Expire stale anonymous carts, purge old ones and notify abandoned carts."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only report what would happen.")

    def handle(self, *args, **options):
        if options["dry_run"]:
            from datetime import timedelta

            from django.utils import timezone

            from flexcommerce_cart.conf import cart_setting
            from flexcommerce_cart.models import Cart

            now = timezone.now()
            expiring = Cart.objects.filter(status=Cart.STATUS_ACTIVE, user__isnull=True, expires_at__lt=now).count()
            cutoff = now - timedelta(hours=cart_setting("CART_ABANDONMENT_HOURS"))
            idle = Cart.objects.filter(
                status=Cart.STATUS_ACTIVE,
                last_activity__lt=cutoff,
                abandoned_notified_at__isnull=True,
                items_count__gt=0,
            ).count()
            self.stdout.write(f"[DRY RUN] Would expire {expiring} carts and check {idle} idle carts.")
            return
        expired = expire_carts(limit=100_000)
        abandoned = notify_abandoned_carts(limit=100_000)
        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {expired['expired']} carts, purged {expired['purged']}, "
                f"notified {abandoned['notified']} abandoned carts."
            )
        )
