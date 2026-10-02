"""Create vendor payouts for earnings whose hold period has passed."""

from django.core.management.base import BaseCommand

from flexcommerce_marketplace.services import generate_payouts


class Command(BaseCommand):
    help = "Create payouts for approved vendors with settled earnings."

    def handle(self, *args, **options):
        result = generate_payouts()
        self.stdout.write(self.style.SUCCESS(f"Created {result['payouts']} payouts."))
