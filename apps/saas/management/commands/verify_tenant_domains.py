"""Verify pending claims and re-check verified custom domains.

Schedule every 15-60 minutes, e.g. ``*/30 * * * * /path/venv/python manage.py verify_tenant_domains``.
Each verified domain is only looked up again after ``domain_reverify_hours`` unless
``--force`` is supplied, so the command is cheap to run frequently.
"""

from django.core.management.base import BaseCommand

from apps.saas.domains import verify_due_domains


class Command(BaseCommand):
    help = "Check DNS TXT ownership for pending and due custom domains."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Check every pending or verified domain now, ignoring the health-check interval.",
        )

    def handle(self, *args, **options):
        result = verify_due_domains(force=options["force"])
        self.stdout.write(
            self.style.SUCCESS(
                "Domain verification complete: "
                f"{result['checked']} checked, "
                f"{result['verified']} verified, "
                f"{result['failed']} still failing "
                f"({result['total']} domain(s) configured)."
            )
        )
