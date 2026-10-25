import json
import os
import sys
from datetime import timedelta
from urllib.parse import urlparse

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core.models import (
    PmDashboard,
    PmFeatureFlag,
    PmReportDefinition,
    Project,
    ProjectChangeRequest,
    ProjectExpense,
    ProjectHandover,
    ProjectMom,
    ProjectTask,
    ProjectTeam,
    ProjectTimeEntry,
    SlaContract,
    SlaHealthCheck,
    SlaTicket,
    User,
)
from apps.core.tenancy import default_tenant, set_current_tenant
from apps.project_management import views as pm_views
from apps.project_management.services import issue_magic_link

set_current_tenant(default_tenant())

# Never touch SMTP from a smoke run.
pm_views.send_pm_email = lambda *args, **kwargs: (True, "")

client = TestClient(headers={"host": "localhost"})
admin = User.objects.get(pk=1)
client.force_login(admin)

today = timezone.localdate()
results = []


def check(label, response, expected=(200, 302)):
    ok = response.status_code in expected
    results.append((label, response.status_code, ok))
    if not ok:
        snippet = response.content[:300].decode("utf-8", "replace")
        print(f"FAIL {label}: {response.status_code} {snippet}")
    return response


def last_id(model, **filters):
    row = model.objects.filter(**filters).order_by("-id").first()
    assert row is not None, f"missing {model.__name__} {filters}"
    return row


with transaction.atomic():
    project = Project.objects.get(project_code="PRJ-2026-001")
    bidding_project = Project.objects.get(project_code="PRJ-2026-003")
    employee = User.objects.get(pk=1).employee_id or 1
    task3 = ProjectTask.objects.get(project_id=project.id, id=3)

    # Project and task lifecycle
    check(
        "project_create",
        client.post(
            "/projects/create",
            {
                "project_code": "",
                "project_name": "Smoke verification project",
                "project_type": "internal_initiative",
                "client_name": "",
                "client_email": "",
                "contract_value": "50000.00",
                "allocated_expense_budget": "5000.00",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=60)).isoformat(),
                "status": "bidding",
                "priority": "medium",
                "description": "Created by the POST smoke test.",
                "manager": str(employee),
            },
        ),
    )
    new_project = last_id(Project, project_name="Smoke verification project")
    check(
        "project_edit",
        client.post(
            f"/projects/edit/{new_project.id}",
            {
                "project_code": new_project.project_code,
                "project_name": "Smoke verification project (edited)",
                "project_type": "internal_initiative",
                "client_name": "",
                "client_email": "",
                "contract_value": "55000.00",
                "allocated_expense_budget": "5000.00",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=60)).isoformat(),
                "status": "bidding",
                "priority": "high",
                "description": "Edited by the POST smoke test.",
                "manager": str(employee),
            },
        ),
    )
    check(
        "task_create",
        client.post(
            f"/projects/{new_project.id}/tasks/create",
            {
                "task_name": "Smoke task",
                "description": "Created by smoke test.",
                "assigned_to": str(employee),
                "start_date": today.isoformat(),
                "deadline_date": (today + timedelta(days=7)).isoformat(),
                "estimated_hours": "8",
                "status": "backlog",
                "priority": "medium",
                "completion_percent": "0",
            },
        ),
    )
    new_task = last_id(ProjectTask, project_id=new_project.id)
    check(
        "task_edit",
        client.post(
            f"/projects/{new_project.id}/tasks/edit/{new_task.id}",
            {
                "task_name": "Smoke task (edited)",
                "description": "Edited by smoke test.",
                "assigned_to": str(employee),
                "start_date": today.isoformat(),
                "deadline_date": (today + timedelta(days=10)).isoformat(),
                "estimated_hours": "12",
                "status": "in_progress",
                "priority": "high",
                "completion_percent": "10",
            },
        ),
    )

    check(
        "task_create_child",
        client.post(
            f"/projects/{project.id}/tasks/create",
            {
                "task_name": "Smoke child task",
                "description": "Child task created by smoke test.",
                "assigned_to": str(employee),
                "parent_task": "1",
                "start_date": today.isoformat(),
                "deadline_date": (today + timedelta(days=5)).isoformat(),
                "estimated_hours": "4",
                "status": "backlog",
                "priority": "low",
                "completion_percent": "0",
            },
        ),
    )
    child_task = last_id(ProjectTask, project_id=project.id)
    assert child_task.parent_task_id == 1, child_task.parent_task_id
    assert child_task.assigned_to == employee, child_task.assigned_to

    # Team lifecycle
    check(
        "team_add",
        client.post(
            f"/projects/{new_project.id}/team",
            {
                "employee": "6",
                "role": "Support engineer",
                "allocation_percentage": "50",
                "hourly_rate": "1500",
                "joined_at": today.isoformat(),
            },
        ),
    )
    member = last_id(ProjectTeam, project_id=new_project.id, employee_id=6)
    check(
        "team_remove",
        client.post(f"/projects/{new_project.id}/team/remove/{member.id}"),
    )
    member.refresh_from_db()
    assert member.left_at is not None, "team member was not marked as left"

    # Timesheets
    check(
        "timesheet_submit",
        client.post(
            f"/projects/{project.id}/timesheets",
            {
                "employee": str(employee),
                "task": str(task3.id),
                "entry_date": today.isoformat(),
                "hours": "2",
                "description": "Smoke test work.",
                "billable": "on",
            },
        ),
    )
    new_entry = last_id(ProjectTimeEntry, project_id=project.id)
    check(
        "time_approve",
        client.post(
            f"/projects/{project.id}/timesheets/{new_entry.id}/decision",
            {"action": "approve"},
        ),
    )
    new_entry.refresh_from_db()
    assert new_entry.status == "approved", new_entry.status

    # Field expenses
    check(
        "expense_submit",
        client.post(
            f"/projects/{project.id}/expenses",
            {
                "expense_date": today.isoformat(),
                "category": "transport",
                "description": "Smoke test transport.",
                "amount": "100.00",
            },
        ),
    )
    new_expense = last_id(ProjectExpense, project_id=project.id)
    check(
        "expense_approve",
        client.post(
            f"/projects/{project.id}/expenses/{new_expense.id}/decision",
            {"action": "approve"},
        ),
    )
    new_expense.refresh_from_db()
    assert new_expense.status == "approved", new_expense.status
    check(
        "expenses_csv",
        client.get(f"/projects/expenses?export=csv"),
    )

    # MOM, change requests and client approvals
    mom_payload = {
        "title": "Smoke test review",
        "meeting_date": today.isoformat(),
        "attendees": "Client, project manager",
        "agenda": "Smoke verification",
        "minutes": "Reviewed the smoke verification checklist.",
        "decisions": "Proceed with verification.",
        "action_items": "QA - confirm - next week",
    }
    check(
        "mom_create",
        client.post(f"/projects/{project.id}/moms/create", mom_payload),
    )
    new_mom = last_id(ProjectMom, project_id=project.id)
    mom_payload["title"] = "Smoke test review (edited)"
    check(
        "mom_edit",
        client.post(f"/projects/{project.id}/moms/edit/{new_mom.id}", mom_payload),
    )
    check(
        "mom_pdf",
        client.get(f"/projects/{project.id}/moms/view/{new_mom.id}/pdf"),
    )
    check(
        "mom_send",
        client.post(
            f"/projects/{project.id}/moms/{new_mom.id}/send",
            {"email": "client@example.com", "message": "Please review the smoke MOM."},
        ),
    )
    new_mom.refresh_from_db()
    assert new_mom.status == "pending_approval", new_mom.status

    check(
        "cr_create",
        client.post(
            f"/projects/{project.id}/change-requests/create",
            {
                "task": str(task3.id),
                "title": "Smoke change request",
                "description": "Additional smoke coverage.",
                "justification": "Verify change request workflow.",
                "cost_impact": "1200.00",
                "schedule_impact_days": "3",
            },
        ),
    )
    new_cr = last_id(ProjectChangeRequest, project_id=project.id)
    assert new_cr.task_id == task3.id, new_cr.task_id
    task3.refresh_from_db()
    assert task3.is_scope_change, "linked task was not flagged as out of scope"
    check(
        "cr_send",
        client.post(
            f"/projects/{project.id}/change-requests/{new_cr.id}/send",
            {"email": "client@example.com", "message": "Please review the smoke CR."},
        ),
    )
    check(
        "uat_send",
        client.post(
            f"/projects/{project.id}/send-uat",
            {"email": "client@example.com", "message": "Please accept UAT."},
        ),
    )

    # Public magic-link decisions
    _, token = issue_magic_link(
        project, "accept_uat", project.id, "client@example.com", admin.id
    )
    check(
        "public_approval_uat",
        client.post(
            f"/projects/approval/{token}",
            {"decision": "approve", "notes": "UAT accepted in smoke test."},
        ),
    )
    project.refresh_from_db()
    assert project.uat_accepted_at is not None, "UAT was not accepted"

    _, token = issue_magic_link(
        project, "approve_mom", new_mom.id, "client@example.com", admin.id
    )
    check(
        "public_approval_mom",
        client.post(
            f"/projects/approval/{token}",
            {"decision": "approve", "notes": "MOM approved in smoke test."},
        ),
    )
    new_mom.refresh_from_db()
    assert new_mom.status == "approved", new_mom.status

    _, token = issue_magic_link(
        project, "approve_change_request", new_cr.id, "client@example.com", admin.id
    )
    check(
        "public_approval_cr",
        client.post(
            f"/projects/approval/{token}",
            {"decision": "reject", "notes": "Rejected in smoke test."},
        ),
    )
    new_cr.refresh_from_db()
    assert new_cr.status == "rejected", new_cr.status

    # Kanban and Gantt
    check(
        "board_move",
        client.post(
            f"/projects/{project.id}/board/move",
            json.dumps(
                {
                    "task_id": task3.id,
                    "status": "in_qa",
                    "ordered_ids": [3, 4, 5, 1, 2, 6],
                }
            ),
            content_type="application/json",
        ),
    )
    check(
        "gantt_reschedule",
        client.post(
            f"/projects/gantt/{project.id}/reschedule/{task3.id}",
            json.dumps({"delta_days": 2}),
            content_type="application/json",
        ),
    )
    check("gantt_pdf", client.get(f"/projects/gantt/{project.id}/download"))

    # Baseline lock and handover
    check(
        "baseline_lock",
        client.post(f"/projects/{bidding_project.id}/lock-baseline"),
    )
    bidding_project.refresh_from_db()
    assert bidding_project.baseline_locked_at is not None, "baseline was not locked"

    handover_count = ProjectHandover.objects.filter(project_id=project.id).count()
    required_tasks = list(
        ProjectTask.objects.filter(
            project_id=project.id, status__in=["in_progress", "blocked", "in_qa"]
        ).values_list("id", flat=True)
    )
    check(
        "handover",
        client.post(
            f"/projects/{project.id}/handover",
            {
                "departing_employee": "2",
                "incoming_employee": "6",
                "task_ids": [str(pk) for pk in required_tasks],
                "state_of_play": (
                    "Smoke handover memo: the departing engineer briefed the incoming "
                    "engineer on delivery status, current risks, repository locations, "
                    "credentials handover locations and next actions for the sprint."
                ),
                "asset_notes": "Repository and credentials documented in the shared vault.",
            },
        ),
    )
    assert (
        ProjectHandover.objects.filter(project_id=project.id).count() == handover_count + 1
    ), "handover was not created"

    # SLA hub
    check(
        "ticket_update",
        client.post(
            f"/projects/sla/tickets/{SlaTicket.objects.get(pk=1).id}/update",
            {"status": "resolved", "assigned_to": str(employee), "note": "Resolved."},
        ),
    )
    scheduled = SlaHealthCheck.objects.filter(status="scheduled").order_by("id").first()
    check(
        "health_check_complete",
        client.post(
            f"/projects/sla/health-checks/{scheduled.id}/complete",
            {"findings": "All preventive checks passed during the smoke run."},
        ),
    )
    check(
        "sla_contract_create",
        client.post(
            "/projects/sla/contracts/create",
            {
                "contract_number": "",
                "project": str(new_project.id),
                "title": "Smoke support retainer",
                "client_name": "Smoke Client Ltd",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=365)).isoformat(),
                "support_tier": "priority",
                "monthly_value": "25000.00",
                "response_target_hours": "4",
                "resolution_target_hours": "24",
                "status": "active",
            },
        ),
    )
    new_contract = last_id(SlaContract, title="Smoke support retainer")
    check(
        "health_check_schedule",
        client.post(
            f"/projects/sla/contracts/{new_contract.id}/schedule-health",
            {
                "start_date": (today + timedelta(days=7)).isoformat(),
                "frequency": "monthly",
                "occurrences": "2",
            },
        ),
    )
    check(
        "sla_ticket_create",
        client.post(
            "/projects/sla/tickets/create",
            {
                "contract": str(new_contract.id),
                "project": str(new_project.id),
                "title": "Smoke ticket",
                "description": "Created by the smoke test.",
                "priority": "high",
                "status": "open",
                "assigned_to": str(employee),
            },
        ),
    )
    check(
        "ticket_detail",
        client.get(f"/projects/sla/tickets/{last_id(SlaTicket).id}"),
    )

    # Analytics, reports and dashboards
    check(
        "analytics_query",
        client.get("/projects/analytics/query?dataset=tasks&dimension=assignee&metric=count"),
    )
    check(
        "analytics_export",
        client.get("/projects/analytics/export?dataset=projects&dimension=status&metric=count"),
    )
    check(
        "report_save",
        client.post(
            "/projects/analytics/reports/save",
            {
                "name": "Smoke saved report",
                "description": "Created by the smoke test.",
                "schedule": "none",
                "recipients": "client@example.com",
                "dataset": "projects",
                "dimension": "status",
                "metric": "count",
                "status": "",
                "date_from": "",
                "date_to": "",
            },
        ),
    )
    new_report = last_id(PmReportDefinition, name="Smoke saved report")
    check("report_detail", client.get(f"/projects/analytics/reports/{new_report.id}"))
    check(
        "report_export",
        client.get(f"/projects/analytics/reports/{new_report.id}/export"),
    )
    check(
        "report_pdf",
        client.get(f"/projects/analytics/reports/{new_report.id}/pdf"),
    )
    check(
        "report_dispatch",
        client.post(f"/projects/analytics/reports/{new_report.id}/dispatch"),
    )
    check(
        "dashboard_create",
        client.post(
            "/projects/dashboards/create",
            {
                "title": "Smoke dashboard",
                "widgets": ["portfolio_summary", "expense_burn", "team_workload"],
                "widget_order": "portfolio_summary,expense_burn,team_workload",
            },
        ),
    )
    new_dashboard = last_id(PmDashboard, title="Smoke dashboard")
    check("dashboard_detail", client.get(f"/projects/dashboards/{new_dashboard.id}"))
    check(
        "dashboard_edit",
        client.post(
            f"/projects/dashboards/{new_dashboard.id}/edit",
            {
                "title": "Smoke dashboard (edited)",
                "widgets": ["sla_snapshot", "recent_activity"],
                "widget_order": "sla_snapshot,recent_activity",
            },
        ),
    )
    check(
        "dashboard_delete",
        client.post(f"/projects/dashboards/{new_dashboard.id}/delete"),
    )
    assert not PmDashboard.objects.filter(pk=new_dashboard.id).exists(), "dashboard not deleted"

    flag = PmFeatureFlag.objects.order_by("id").first()
    previous = flag.enabled
    check("flag_toggle", client.post(f"/projects/license/{flag.id}/toggle"))
    flag.refresh_from_db()
    assert flag.enabled != previous, "feature flag did not toggle"

    transaction.set_rollback(True)

failures = [row for row in results if not row[2]]
for label, status, ok in results:
    print(f"{'OK ' if ok else 'FAIL'} {status:>3}  {label}")
print(f"\n{len(results) - len(failures)}/{len(results)} POST workflows passed; all changes rolled back.")
if failures:
    sys.exit(1)
