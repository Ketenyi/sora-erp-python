"""Verify row-level tenant isolation across ORM, HTTP and public links.

Everything runs inside a transaction that is rolled back, so the seeded
workspace and demo portfolio are never modified.
"""

import os
import sys
import uuid
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core.models import Client, Project, ProjectMagicLink, User
from apps.core.tenancy import (
    TenantContextRequired,
    all_tenants_context,
    default_tenant,
    tenant_context,
)
from apps.core.services import next_code
from apps.project_management.services import issue_magic_link, resolve_magic_link
from apps.saas.models import Tenant
from apps.saas.services import provision_tenant

results = []
DEMO_MARKER = "Isolation Demo Marker Project"


def check(label, condition, detail=""):
    results.append((label, bool(condition)))
    print(f"{'OK  ' if condition else 'FAIL'} {label}" + ("" if condition else f"  [{detail}]"))


def raises_required(label, callback):
    try:
        callback()
    except TenantContextRequired:
        check(label, True)
    except Exception as exc:  # noqa: BLE001 - report unexpected failure shapes
        check(label, False, f"{type(exc).__name__}: {exc}")
    else:
        check(label, False, "no TenantContextRequired raised")


stamp = uuid.uuid4().hex[:8]
now = timezone.now()
today = timezone.localdate()
demo = default_tenant()
assert demo is not None, "Demo workspace missing; run migrations first."

with transaction.atomic():
    provisioned = provision_tenant(
        company_name=f"Isolation Test {stamp}",
        admin_email=f"owner-{stamp}@isolation.test",
        password="Isolation123!",
        first_name="Iso",
        last_name="Owner",
        module_codes=["SORA_PROJECTS"],
        trial_days=30,
    )
    other = provisioned["tenant"]
    other_user = provisioned["user"]

    with tenant_context(demo):
        demo_project = Project.objects.get(project_code="PRJ-2026-001")
        demo_client = Client.objects.order_by("id").first()
        demo_counts = {
            "projects": Project.objects.count(),
            "clients": Client.objects.count(),
        }
        demo_marker = Project.objects.create(
            project_code="PRJ-9999",
            project_name=DEMO_MARKER,
            project_type="internal_initiative",
            status="bidding",
            priority="medium",
            start_date=today,
            end_date=today + timedelta(days=30),
            created_at=now,
        )
        demo_next_code = next_code("PRJ", "projects", "project_code")

    with tenant_context(other):
        other_client = Client.objects.create(
            client_code="CLI-0001",
            company_name="Isolation Alpha Client",
            email="alpha@isolation.test",
        )
        other_project = Project.objects.create(
            project_code="PRJ-2026-001",
            project_name="Isolation Alpha Project",
            project_type="internal_initiative",
            status="bidding",
            priority="medium",
            start_date=today,
            end_date=today + timedelta(days=30),
            created_at=now,
        )
        other_next_code = next_code("PRJ", "projects", "project_code")
        other_project_count = Project.objects.count()
        link, raw_token = issue_magic_link(
            other_project,
            "accept_uat",
            other_project.id,
            recipient_email="alpha@isolation.test",
        )

    check("tenant_scoped_unique_code_reused", other_project.project_code == demo_project.project_code)
    check("other_tenant_new_workspace_empty", other_project_count == 1, other_project_count)
    check(
        "next_code_scoped_to_workspace",
        other_next_code == "PRJ-0002" and demo_next_code == "PRJ-10000",
        (other_next_code, demo_next_code),
    )

    with tenant_context(demo):
        check("demo_cannot_see_other_project", not Project.objects.filter(pk=other_project.pk).exists())
        check("demo_cannot_see_other_client", not Client.objects.filter(pk=other_client.pk).exists())
        check("demo_cannot_see_other_magic_link", not ProjectMagicLink.objects.filter(pk=link.pk).exists())
        check("demo_count_unchanged_by_other_tenant", Project.objects.count() == demo_counts["projects"] + 1)
        check(
            "demo_keeps_only_its_own_code",
            Project.objects.filter(project_code="PRJ-2026-001").count() == 1,
        )

    with tenant_context(other):
        check("other_cannot_see_demo_project", not Project.objects.filter(pk=demo_project.pk).exists())
        check("other_cannot_see_demo_marker", not Project.objects.filter(project_name=DEMO_MARKER).exists())
        check("other_cannot_see_demo_client", not Client.objects.filter(pk=demo_client.pk).exists())
        check("other_sees_own_magic_link", ProjectMagicLink.objects.filter(pk=link.pk).exists())

    check("no_context_reads_are_empty", Project.objects.count() == 0)
    check("no_context_clients_are_empty", Client.objects.count() == 0)
    raises_required(
        "no_context_create_blocked",
        lambda: Project.objects.create(project_code="PRJ-NOCTX", project_name="No context"),
    )
    raises_required(
        "no_context_bulk_create_blocked",
        lambda: Project.objects.bulk_create([Project(project_code="PRJ-BULK", project_name="Bulk")]),
    )

    with tenant_context(demo):
        raises_required(
            "cross_tenant_create_blocked",
            lambda: Project.objects.create(
                tenant=other,
                project_code="PRJ-STEAL",
                project_name="Wrong workspace",
            ),
        )
        raises_required(
            "cross_tenant_bulk_create_blocked",
            lambda: Project.objects.bulk_create(
                [Project(tenant=other, project_code="PRJ-STEAL2", project_name="Wrong workspace")]
            ),
        )
        check(
            "all_tenants_ignores_active_context",
            Project.objects.all_tenants().filter(pk=other_project.pk).exists(),
        )
        check(
            "all_tenants_chain_stays_unscoped",
            Project.objects.all_tenants().filter(project_name="Isolation Alpha Project").exists(),
        )
        check(
            "both_workspaces_reuse_same_natural_key",
            Project.objects.all_tenants().filter(project_code="PRJ-2026-001").count() == 2,
        )

    with all_tenants_context():
        check(
            "all_tenants_sees_both_workspaces",
            Project.objects.filter(pk__in=[demo_project.pk, other_project.pk]).count() == 2,
        )
        check("all_tenants_magic_link_visible", ProjectMagicLink.objects.filter(pk=link.pk).exists())

    resolved = resolve_magic_link(raw_token)
    check("public_magic_link_resolves_without_context", resolved is not None and resolved.pk == link.pk)

    public = TestClient(headers={"host": "localhost"})
    approval = public.get(f"/projects/approval/{raw_token}")
    approval_text = approval.content.decode("utf-8", "replace")
    check("public_approval_ok", approval.status_code == 200, approval.status_code)
    check("public_approval_shows_own_project", "Isolation Alpha Project" in approval_text)
    check("public_approval_hides_demo_project", DEMO_MARKER not in approval_text)

    owner = TestClient(headers={"host": "localhost"})
    owner.force_login(other_user)
    portfolio = owner.get("/projects/portfolio")
    portfolio_text = portfolio.content.decode("utf-8", "replace")
    check("tenant_portfolio_ok", portfolio.status_code == 200, portfolio.status_code)
    check("tenant_portfolio_shows_own_project", "Isolation Alpha Project" in portfolio_text)
    check("tenant_portfolio_hides_demo_projects", DEMO_MARKER not in portfolio_text)
    demo_detail = owner.get(f"/projects/{demo_project.pk}")
    check(
        "tenant_project_detail_hides_demo",
        demo_detail.status_code == 404,
        demo_detail.status_code,
    )

    demo_user = User.objects.get(pk=1)
    demo_client_http = TestClient(headers={"host": "localhost"})
    demo_client_http.force_login(demo_user)
    session = demo_client_http.session
    session["sora_tenant_id"] = other.id
    session.save()
    demo_portfolio = demo_client_http.get("/projects/portfolio")
    demo_portfolio_text = demo_portfolio.content.decode("utf-8", "replace")
    check("foreign_session_tenant_rejected", demo_portfolio.status_code == 200, demo_portfolio.status_code)
    check("foreign_session_keeps_demo_data", DEMO_MARKER in demo_portfolio_text)
    check("foreign_session_blocks_other_data", "Isolation Alpha Project" not in demo_portfolio_text)

    transaction.set_rollback(True)

failures = [label for label, ok in results if not ok]
print(f"\n{len(results) - len(failures)}/{len(results)} isolation checks passed; all changes rolled back.")
if failures:
    sys.exit(1)
