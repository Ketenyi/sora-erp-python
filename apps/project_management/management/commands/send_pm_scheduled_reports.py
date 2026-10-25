"""Dispatch due project analytics reports by email with CSV and PDF attachments."""

from __future__ import annotations

import csv
import io
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import PmReportDefinition
from apps.core.tenancy import tenant_context
from apps.project_management.pdf import report_pdf
from apps.project_management.services import query_csv_rows, run_query, send_pm_email
from apps.saas.models import Tenant

SCHEDULE_INTERVALS = {
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


def _recipients(report):
    raw = (report.recipients or "").replace(";", ",")
    return [value.strip() for value in raw.split(",") if value.strip()]


def _csv_content(result):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([result["dimension_label"], result["metric_label"], "% of max"])
    writer.writerows(query_csv_rows(result))
    return buffer.getvalue()


def _filters_text(result):
    parts = [f"Dataset: {result['dataset_label']}", f"Dimension: {result['dimension_label']}"]
    parts.append(f"Metric: {result['metric_label']}")
    if result["status"]:
        parts.append(f"Status: {result['status'].replace('_', ' ').title()}")
    if result["date_from"] or result["date_to"]:
        parts.append(f"Dates: {result['date_from'] or 'any'} to {result['date_to'] or 'any'}")
    return " · ".join(parts)


def _is_due(report, now):
    interval = SCHEDULE_INTERVALS.get(report.schedule)
    if interval is None:
        return False
    last_run = report.last_run_at
    if last_run is None:
        return True
    return now - last_run >= interval


class Command(BaseCommand):
    help = "Email project analytics reports that are due for weekly or monthly delivery."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Dispatch all scheduled reports regardless of the last run time.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent without sending or updating last-run times.",
        )
        parser.add_argument(
            "--report-id",
            type=int,
            help="Only process a single report definition by ID.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        self.sent_total = 0
        self.skipped = 0
        self.failed = 0

        for tenant in Tenant.objects.order_by("id"):
            with tenant_context(tenant):
                self._process_tenant(tenant, now, options)

        suffix = " (dry run)" if options["dry_run"] else ""
        self.stdout.write(self.style.SUCCESS(
            f"Scheduled report run complete{suffix}: {self.sent_total} email(s) sent, "
            f"{self.skipped} report(s) skipped, {self.failed} failure(s)."
        ))

    def _process_tenant(self, tenant, now, options):
        reports = PmReportDefinition.objects.exclude(schedule="none")
        if options.get("report_id"):
            reports = reports.filter(pk=options["report_id"])
        reports = reports.order_by("id")

        for report in reports:
            if not options["force"] and not _is_due(report, now):
                self.skipped += 1
                continue
            recipients = _recipients(report)
            if not recipients:
                self.stdout.write(self.style.WARNING(
                    f"[{tenant.slug}] Report #{report.id} “{report.name}” has no recipients — skipped."
                ))
                self.skipped += 1
                continue

            result = run_query(report.filters or {})
            csv_content = _csv_content(result)
            pdf_content = report_pdf(
                report.name,
                f"SORA ERP · {result['dataset_label']}",
                [result["dimension_label"], result["metric_label"], "% of max"],
                [
                    [row["label"], f"{row['value']:,.2f}", f"{row['percent']}%"]
                    for row in result["rows"]
                ],
                _filters_text(result),
            )
            body = (
                f"Hello,\n\nPlease find attached the scheduled PM report “{report.name}”.\n\n"
                f"{_filters_text(result)}\n\nRegards,\nSORA ERP"
            )

            if options["dry_run"]:
                self.stdout.write(
                    f"[dry-run] [{tenant.slug}] Would send “{report.name}” "
                    f"to {', '.join(recipients)}."
                )
                continue

            delivered = 0
            for recipient in recipients:
                ok, error = send_pm_email(
                    subject=f"SORA PM report · {report.name}",
                    body=body,
                    recipient=recipient,
                    attachments=[
                        (f"{report.name}.csv", csv_content, "text/csv"),
                        (f"{report.name}.pdf", pdf_content, "application/pdf"),
                    ],
                )
                if ok:
                    delivered += 1
                else:
                    self.failed += 1
                    self.stderr.write(self.style.ERROR(
                        f"[{tenant.slug}] Delivery failed for {recipient} "
                        f"({report.name}): {error}"
                    ))
            if delivered:
                self.sent_total += delivered
                report.last_run_at = now
                report.save(update_fields=["last_run_at"])
                self.stdout.write(self.style.SUCCESS(
                    f"[{tenant.slug}] Report “{report.name}” sent to {delivered} recipient(s)."
                ))
