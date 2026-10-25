import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient

from apps.core.models import PmFeatureFlag, Project, User
from apps.core.tenancy import default_tenant, set_current_tenant

set_current_tenant(default_tenant())

failures = []


def session(user_id):
    client = TestClient(headers={"host": "localhost"})
    client.force_login(User.objects.get(pk=user_id))
    return client


def expect(label, response, status):
    ok = response.status_code == status
    if not ok:
        failures.append((label, response.status_code, status))
    print(f"{'OK ' if ok else 'FAIL'} {response.status_code:>3} (want {status})  {label}")
    return response


admin = session(1)
manager = session(5)
viewer = session(3)
employee = session(6)

admin_license = expect("admin license page", admin.get("/projects/license"), 200)
expect("manager license page denied", manager.get("/projects/license"), 403)
expect("viewer license page denied", viewer.get("/projects/license"), 403)
expect("employee license page denied", employee.get("/projects/license"), 403)

admin_shell = admin.get("/dashboard").content.decode()
manager_shell = manager.get("/dashboard").content.decode()
viewer_shell = viewer.get("/projects/portfolio").content.decode()
employee_shell = employee.get("/projects/my-tasks").content.decode()

menu_checks = [
    ("admin sees License & Features", "/projects/license" in admin_shell, True),
    ("manager does not see License & Features", "/projects/license" in manager_shell, False),
    ("viewer sees My Dashboards", "My Dashboards" in viewer_shell, True),
    ("employee sees My Work", "My Work" in employee_shell, True),
    ("employee sees Handover menu", "Handover" in employee_shell, True),
    ("viewer shell has no Handover menu", "Handover" in viewer_shell, False),
    ("license page has tier groups", "Core" in admin_license.content.decode(), True),
]
for label, actual, wanted in menu_checks:
    ok = actual == wanted
    if not ok:
        failures.append((label, actual, wanted))
    print(f"{'OK ' if ok else 'FAIL'} {label}")

expect("viewer portfolio", viewer.get("/projects/portfolio"), 200)
expect("viewer dashboards", viewer.get("/projects/dashboards"), 200)
expect("viewer analytics", viewer.get("/projects/analytics"), 200)
expect("viewer cannot open project create", viewer.get("/projects/create"), 302)
expect("employee my tasks", employee.get("/projects/my-tasks"), 200)
expect("employee scorecards", employee.get("/projects/scorecards"), 200)

with transaction.atomic():
    before = Project.objects.count()
    project = Project.objects.order_by("id").first()
    response = viewer.post(
        "/projects/create",
        {
            "project_name": "Viewer must not create",
            "project_type": "internal_initiative",
            "contract_value": "1",
            "allocated_expense_budget": "1",
            "start_date": project.start_date.isoformat(),
            "end_date": project.end_date.isoformat(),
            "status": "bidding",
            "priority": "medium",
            "manager": "1",
        },
    )
    expect("viewer create is blocked", response, 302)
    unchanged = Project.objects.count() == before
    print(f"{'OK ' if unchanged else 'FAIL'} viewer POST created no project")
    if not unchanged:
        failures.append(("viewer create persisted", Project.objects.count(), before))

    analytics_flag = PmFeatureFlag.objects.get(flag_key="MODULE_ANALYTICS")
    analytics_flag.enabled = False
    analytics_flag.save(update_fields=["enabled", "updated_at"])
    response = admin.get("/projects/analytics")
    blocked = response.status_code == 302 and response.headers.get("Location") == "/projects/dashboard"
    print(f"{'OK ' if blocked else 'FAIL'} disabled licence flag blocks analytics")
    if not blocked:
        failures.append(("flag gate", response.status_code, response.headers.get("Location")))

    transaction.set_rollback(True)

print(f"\n{len(failures)} RBAC/menu failures.")
raise SystemExit(1 if failures else 0)
