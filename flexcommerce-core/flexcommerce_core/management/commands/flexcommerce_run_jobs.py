"""Run FlexCommerce maintenance jobs (expire carts, release stock, retries, ...)."""

from django.core.management.base import BaseCommand

from flexcommerce_core.jobs import get_jobs, run_due_jobs


class Command(BaseCommand):
    help = "Run all due FlexCommerce maintenance jobs. Schedule every few minutes via cron."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run jobs even if their interval has not elapsed.")
        parser.add_argument("--only", nargs="*", help="Only run these job names.")
        parser.add_argument("--list", action="store_true", help="List registered jobs and exit.")

    def handle(self, *args, **options):
        if options["list"]:
            for name, job in sorted(get_jobs().items()):
                self.stdout.write(f"{name:<32} every {job.interval_minutes:>5} min  {job.description}")
            return
        results = run_due_jobs(force=options["force"], only=options["only"])
        failed = [name for name, result in results.items() if isinstance(result, Exception)]
        for name, result in results.items():
            style = self.style.ERROR if name in failed else self.style.SUCCESS
            self.stdout.write(style(f"{name}: {result}"))
        if not results:
            self.stdout.write("No jobs were due.")
