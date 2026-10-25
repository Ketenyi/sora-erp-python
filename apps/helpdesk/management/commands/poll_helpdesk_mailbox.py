"""Poll shared helpdesk mailboxes and turn new messages into tickets.

Run from cron / Task Scheduler every few minutes, for example:

    */2 * * * * cd /srv/sora && .venv/bin/python manage.py poll_helpdesk_mailbox

Use ``--tenant`` to drain one workspace or omit it to poll every active
workspace mailbox in the control plane.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.core.tenancy import all_tenants_context, tenant_context
from apps.helpdesk import services
from apps.saas.models import Tenant


class Command(BaseCommand):
    help = "Fetch new helpdesk mailbox messages and ingest them as tickets or replies."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="Workspace slug to poll (default: all).")
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum messages to ingest per mailbox (default: 50).",
        )

    def handle(self, *args, **options):
        limit = max(1, min(options["limit"] or 50, 500))
        slug = (options.get("tenant") or "").strip()
        if slug:
            with all_tenants_context():
                tenant = Tenant.objects.filter(slug=slug).first()
            if tenant is None:
                raise CommandError(f"Unknown workspace '{slug}'.")
            with tenant_context(tenant):
                setting = services.helpdesk_setting()
                if setting is None or setting.mailbox_identity is None:
                    raise CommandError(
                        f"Workspace '{slug}' has no helpdesk mailbox configured."
                    )
                summary = services.poll_mailbox(setting, limit=limit)
                self._report(tenant.slug, summary)
                if summary["errors"]:
                    raise CommandError(
                        f"Mailbox poll failed: {setting.last_poll_error[:500]}"
                    )
            return

        results = services.poll_all_mailboxes(limit=limit)
        if not results:
            self.stdout.write(self.style.WARNING("No active helpdesk mailboxes to poll."))
            return
        failures = 0
        for row in results:
            self._report(row["tenant"], row)
            failures += int(bool(row["errors"]))
        if failures:
            raise CommandError(f"{failures} mailbox poll(s) failed.")

    def _report(self, tenant_slug: str, summary: dict) -> None:
        self.stdout.write(
            self.style.SUCCESS(
                f"[{tenant_slug}] processed={summary['processed']} "
                f"created={summary['created']} duplicates={summary['duplicates']} "
                f"ignored={summary['ignored']} errors={summary['errors']}"
            )
        )
