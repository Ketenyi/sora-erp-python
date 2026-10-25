"""Delete security audit rows older than the configured retention window."""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import LoginAttempt, RequestAudit
from apps.core.security import security_policy


class Command(BaseCommand):
    help = "Purge login attempts and request audits older than the retention policy."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override the security_audit_retention_days platform setting.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would be deleted without deleting them.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Rows removed per statement (default 5000).",
        )

    def handle(self, *args, **options):
        days = options["days"] if options["days"] is not None else security_policy()["audit_retention_days"]
        if days < 1:
            raise CommandError("Retention must be at least one day.")
        batch_size = max(1, options["batch_size"])
        cutoff = timezone.now() - timedelta(days=days)
        dry_run = options["dry_run"]

        self.stdout.write(f"Retention: {days} day(s); cutoff {cutoff.isoformat()}.")
        total = 0
        for model in (LoginAttempt, RequestAudit):
            queryset = model.objects.filter(created_at__lt=cutoff)
            pending = queryset.count()
            if dry_run:
                total += pending
                self.stdout.write(f"{model.__name__}: {pending} row(s) would be deleted.")
                continue
            deleted = 0
            while True:
                ids = list(queryset.values_list("pk", flat=True)[:batch_size])
                if not ids:
                    break
                removed, _ = model.objects.filter(pk__in=ids).delete()
                deleted += removed
                self.stdout.write(f"{model.__name__}: removed {deleted}/{pending}...")
            total += deleted
            self.stdout.write(f"{model.__name__}: {deleted} row(s) deleted.")

        verb = "would be" if dry_run else "were"
        self.stdout.write(self.style.SUCCESS(f"{total} row(s) {verb} deleted."))
