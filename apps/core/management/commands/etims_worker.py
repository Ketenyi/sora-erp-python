"""Transmit queued eTIMS receipts for every workspace.

Run from cron or a systemd timer, for example every two minutes:
    python manage.py etims_worker --limit 100
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.etims import process_due, recover_stale
from apps.core.models import EtimsSubmission
from apps.core.tenancy import all_tenants_context, tenant_context
from apps.saas.models import Tenant


class Command(BaseCommand):
    help = "Submit pending KRA eTIMS receipts in the background."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="Maximum receipts per workspace.")
        parser.add_argument("--tenant", default="", help="Only process this tenant id or slug.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignore retry backoff and attempt every queued receipt now.",
        )

    def handle(self, *args, **options):
        limit = max(1, min(options["limit"], 500))
        target = (options.get("tenant") or "").strip()
        with all_tenants_context():
            tenants = list(Tenant.objects.filter(status="active").order_by("id"))
            if target:
                tenants = [
                    tenant
                    for tenant in tenants
                    if str(tenant.pk) == target or tenant.slug == target
                ]
            stale = recover_stale()
        totals = {"submitted": 0, "failed": 0, "pending": 0, "cancelled": 0}
        for tenant in tenants:
            with tenant_context(tenant):
                if options["force"]:
                    EtimsSubmission.objects.filter(
                        status__in=["pending", "failed"]
                    ).update(next_attempt_at=timezone.now())
                counts = process_due(limit=limit)
                for key, value in counts.items():
                    totals[key] += value
                if any(counts.values()):
                    self.stdout.write(
                        f"{tenant.slug}: submitted={counts['submitted']} failed={counts['failed']} "
                        f"pending={counts['pending']} cancelled={counts['cancelled']}"
                    )
        self.stdout.write(
            "eTIMS worker complete: "
            f"{totals['submitted']} submitted, {totals['failed']} failed, "
            f"{totals['pending']} waiting, {totals['cancelled']} cancelled"
            + (f", {stale} stale recovered" if stale else "")
            + "."
        )
