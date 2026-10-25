import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.test import Client as TestClient

from apps.core.models import ModuleAccessGrant, Project, User
from apps.core.tenancy import default_tenant, set_current_tenant
from apps.saas.models import TenantMembership

tenant = default_tenant()
set_current_tenant(tenant)

failures = []


def session(user):
    client = TestClient(headers={"host": "localhost"})
    client.force_login(user)
    return client


def expect(label, response, status):
    ok = response.status_code == status
    if not ok:
        failures.append((label, response.status_code, status))
    print(f"{'OK ' if ok else 'FAIL'} {response.status_code:>3} (want {status})  {label}")
    return response


def check(label, actual, wanted=True):
    ok = actual == wanted
    if not ok:
        failures.append((label, actual, wanted))
    print(f"{'OK ' if ok else 'FAIL'} {label}" + ("" if ok else f"  ({actual!r})"))


admin = User.objects.get(pk=1)

with transaction.atomic():
    grant_user = User.objects.create(
        username="smoke_grant_viewer",
        email="smoke.grant.viewer@example.com",
        password=make_password("SmokeGrants123!"),
        first_name="Grant",
        last_name="Viewer",
        role=None,
        status="active",
    )
    ppm_user = User.objects.create(
        username="smoke_grant_ppm",
        email="smoke.grant.ppm@example.com",
        password=make_password("SmokeGrants123!"),
        first_name="Ppm",
        last_name="Manager",
        role=None,
        status="active",
    )
    for user in (grant_user, ppm_user):
        TenantMembership.objects.create(tenant=tenant, user=user, role="member")

    admin_client = session(admin)
    viewer = session(grant_user)
    ppm = session(ppm_user)

    access_page = expect(
        "admin opens module access", admin_client.get("/settings/access"), 200
    )
    check("access page renders grants table", "Module Access" in access_page.content.decode())

    response = admin_client.post(
        "/settings/access/create",
        {
            "user": grant_user.id,
            "module": "helpdesk",
            "submodule": "tickets",
            "role": "viewer",
            "scope": "tenant",
            "is_active": "on",
        },
    )
    expect("viewer grant created", response, 302)
    grant = ModuleAccessGrant.objects.filter(user=grant_user, module="helpdesk").first()
    check("viewer grant persisted", grant is not None)
    check("viewer grant role", grant.role if grant else None, "viewer")
    check("viewer grant submodule", grant.submodule if grant else None, "tickets")

    response = admin_client.post(
        "/settings/access/create",
        {
            "user": grant_user.id,
            "module": "helpdesk",
            "submodule": "tickets",
            "role": "viewer",
            "scope": "tenant",
            "is_active": "on",
        },
    )
    expect("duplicate grant rejected", response, 200)
    check(
        "duplicate error shown",
        "identical grant already exists" in response.content.decode(),
    )
    check(
        "duplicate not persisted",
        ModuleAccessGrant.objects.filter(user=grant_user, module="helpdesk").count(),
        1,
    )

    response = admin_client.post(
        "/settings/access/create",
        {
            "user": grant_user.id,
            "module": "helpdesk",
            "submodule": "not_a_real_submodule",
            "role": "viewer",
            "scope": "tenant",
            "is_active": "on",
        },
    )
    expect("invalid submodule rejected", response, 200)
    check(
        "invalid submodule error",
        "Select a valid choice." in response.content.decode(),
    )

    expect("viewer opens ticket queue", viewer.get("/helpdesk/tickets"), 200)
    expect("viewer blocked from helpdesk dashboard", viewer.get("/helpdesk/dashboard"), 403)
    expect("viewer blocked from sla contracts", viewer.get("/helpdesk/contracts"), 403)
    expect("viewer blocked from sales", viewer.get("/sales/quotes"), 403)
    expect("viewer blocked from new ticket page", viewer.get("/helpdesk/tickets/new"), 403)
    expect("viewer blocked from settings", viewer.get("/settings/access"), 403)
    expect(
        "viewer blocked from ticket POST",
        viewer.post("/helpdesk/tickets/new", {"subject": "Nope", "description": "Nope"}),
        403,
    )
    check(
        "viewer POST created no ticket",
        ModuleAccessGrant.objects.filter(user=grant_user, module="helpdesk").count() == 1,
    )

    shell = viewer.get("/helpdesk/tickets").content.decode()
    check("sidebar shows granted module", "Helpdesk &amp; SLA" in shell or "Helpdesk & SLA" in shell)
    check("sidebar shows granted submodule", "Ticket Queue" in shell)
    check("sidebar hides ungranted create", "New Ticket" not in shell)
    check("sidebar hides ungranted submodule", "SLA Contracts" not in shell)
    check(
        "sidebar hides ungranted modules",
        "Finance &amp; Accounts" not in shell and "Sales &amp; CRM" not in shell,
    )

    grant.role = "editor"
    grant.save(update_fields=["role", "updated_at"])
    expect("editor opens ticket queue", viewer.get("/helpdesk/tickets"), 200)
    expect("editor opens new ticket page", viewer.get("/helpdesk/tickets/new"), 200)
    expect("editor cannot open dashboard", viewer.get("/helpdesk/dashboard"), 403)
    expect("editor still blocked from contracts", viewer.get("/helpdesk/contracts"), 403)
    editor_shell = viewer.get("/helpdesk/tickets").content.decode()
    check("editor sees new ticket link", "New Ticket" in editor_shell)

    response = admin_client.post(
        "/settings/access/edit/%s" % grant.id,
        {
            "user": grant_user.id,
            "module": "helpdesk",
            "submodule": "tickets",
            "role": "approver",
            "scope": "tenant",
            "is_active": "on",
        },
    )
    expect("grant edit redirects", response, 302)
    grant.refresh_from_db()
    check("grant role updated", grant.role, "approver")
    expect("approver can read queue", viewer.get("/helpdesk/tickets"), 200)
    expect("approver cannot open create page", viewer.get("/helpdesk/tickets/new"), 403)

    project_one = Project.objects.order_by("id").first()
    project_two = Project.objects.exclude(pk=project_one.pk).order_by("id").first()
    response = admin_client.post(
        "/settings/access/create",
        {
            "user": ppm_user.id,
            "module": "projects",
            "submodule": "portfolio",
            "role": "manager",
            "scope": "project",
            "project": project_one.id,
            "is_active": "on",
        },
    )
    expect("ppm grant created", response, 302)
    ppm_grant = ModuleAccessGrant.objects.filter(user=ppm_user, module="projects").first()
    check("ppm grant scoped to project", ppm_grant.project_id if ppm_grant else None, project_one.id)

    portfolio = expect("ppm opens portfolio", ppm.get("/projects/portfolio"), 200)
    body = portfolio.content.decode()
    check("ppm sees assigned project", project_one.project_name in body)
    check("ppm does not see other project", project_two.project_name not in body)
    expect("ppm opens assigned project", ppm.get(f"/projects/{project_one.id}"), 200)
    expect("ppm blocked from other project", ppm.get(f"/projects/{project_two.id}"), 404)
    expect("ppm blocked from project create", ppm.get("/projects/create"), 302)
    check(
        "ppm create did not persist",
        not Project.objects.filter(project_name="PPM must not create").exists(),
    )

    expect("ppm blocked from sales", ppm.get("/sales/quotes"), 403)
    ppm_shell = ppm.get("/projects/portfolio").content.decode()
    check("ppm sidebar shows projects", "Projects" in ppm_shell)
    check("ppm sidebar hides finance", "Finance &amp; Accounts" not in ppm_shell)

    response = admin_client.post(f"/settings/access/delete/{ppm_grant.id}")
    expect("ppm grant deleted", response, 302)
    check(
        "ppm grant removed",
        not ModuleAccessGrant.objects.filter(pk=ppm_grant.id).exists(),
    )
    expect("ppm loses project access", ppm.get("/projects/portfolio"), 403)

    expect("legacy sales keeps module", session(User.objects.get(pk=2)).get("/sales/quotes"), 200)
    expect(
        "legacy manager keeps projects",
        session(User.objects.get(pk=5)).get("/projects/portfolio"),
        200,
    )

    transaction.set_rollback(True)

print(f"\n{len(failures)} grant failures.")
raise SystemExit(1 if failures else 0)
