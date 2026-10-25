"""Move legacy uploads into per-tenant folders and update stored references."""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.models import (
    Candidate,
    CompanySetting,
    DocumentVersion,
    EmployeeDocument,
    LeaveRequest,
    ProjectExpense,
)


class Command(BaseCommand):
    help = "Relocate legacy /uploads files into tenants/<id>/ folders."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would move without touching files or rows.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        self.media_root = Path(settings.MEDIA_ROOT)
        self.dry_run = dry_run
        self.moved = 0
        self.updated = 0
        self.missing = 0

        self._relocate(ProjectExpense, "receipt_path", "expenses")
        self._relocate(LeaveRequest, "attachment", "leave")
        self._relocate(Candidate, "resume_path", "resumes")
        self._relocate(EmployeeDocument, "file_path", "hr")
        self._relocate(DocumentVersion, "file_path", "documents")
        self._company_logos()

        verb = "Would move" if dry_run else "Moved"
        self.stdout.write(
            f"{verb} {self.moved} file(s), updated {self.updated} reference(s), "
            f"{self.missing} missing file(s)."
        )

    def _relocate(self, model, field: str, category: str) -> None:
        rows = list(
            model.objects.unscoped()
            .exclude(**{f"{field}__isnull": True})
            .exclude(**{field: ""})
            .values_list("pk", "tenant_id", field)
        )
        groups: dict[tuple[int, str], list[int]] = {}
        for pk, tenant_id, value in rows:
            if not value or value.startswith("tenants/") or value.startswith("http"):
                continue
            groups.setdefault((tenant_id, value), []).append(pk)

        for (tenant_id, value), pks in groups.items():
            new_path = self._move(tenant_id, category, value)
            if not new_path:
                continue
            if not self.dry_run:
                self.updated += model.objects.unscoped().filter(pk__in=pks).update(
                    **{field: new_path}
                )

    def _company_logos(self) -> None:
        rows = list(
            CompanySetting.objects.filter(setting_key="company_logo")
            .exclude(setting_value__isnull=True)
            .exclude(setting_value="")
            .values_list("pk", "tenant_id", "setting_value")
        )
        for pk, tenant_id, value in rows:
            if tenant_id is None:
                continue
            if value.startswith("tenants/") or value.startswith(("http", "/static/")):
                continue
            clean = value[len("/uploads/"):] if value.startswith("/uploads/") else value
            new_path = self._move(tenant_id, "branding", clean)
            if new_path and not self.dry_run:
                CompanySetting.objects.filter(pk=pk).update(
                    setting_value=f"/uploads/{new_path}"
                )
                self.updated += 1

    def _move(self, tenant_id: int, category: str, value: str) -> str | None:
        relative = value[len("/uploads/"):] if value.startswith("/uploads/") else value
        source = self.media_root / relative
        if not source.is_file():
            self.missing += 1
            self.stderr.write(f"Missing file: {relative}")
            return None
        destination = self.media_root / "tenants" / str(tenant_id) / category / source.name
        if destination.exists():
            destination = destination.with_stem(f"{destination.stem}-{source.stat().st_mtime_ns}")
        if self.dry_run:
            self.moved += 1
            self.stdout.write(f"Would move {relative} -> {destination.relative_to(self.media_root)}")
            return destination.relative_to(self.media_root).as_posix()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        self.moved += 1
        self.stdout.write(f"Moved {relative} -> {destination.relative_to(self.media_root)}")
        return destination.relative_to(self.media_root).as_posix()
