"""Expire overdue trials, subscriptions and grace periods.

Schedule daily, e.g. ``0 1 * * * /path/venv/python manage.py expire_trials``.
"""

from django.core.management.base import BaseCommand

from apps.saas.services import expire_overdue_tenants


class Command(BaseCommand):
    help = "Flip expired trials, subscriptions and grace periods to the locked state."

    def handle(self, *args, **options):
        result = expire_overdue_tenants()
        self.stdout.write(
            self.style.SUCCESS(
                "Expiry run complete: "
                f"{result['trials']} trial(s), "
                f"{result['subscriptions']} subscription(s), "
                f"{result['grace']} grace period(s) expired "
                f"({result['total']} total)."
            )
        )
