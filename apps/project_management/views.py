"""Project management views: delivery, scope, financials, SLA and analytics."""

from __future__ import annotations

import calendar
import csv
import io
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import (
    Employee,
    PmDashboard,
    PmFeatureFlag,
    PmReportDefinition,
    Project,
    ProjectAuditLog,
    ProjectChangeRequest,
    ProjectExpense,
    ProjectHandover,
    ProjectHandoverItem,
    ProjectMagicLink,
    ProjectMom,
    ProjectTask,
    ProjectTeam,
    ProjectTimeEntry,
    SlaContract,
    SlaHealthCheck,
    SlaTicket,
    User,
    Vendor,
)
from apps.core.permissions import (
    admin_required,
    can_access_project,
    get_access,
    module_access_required,
    visible_projects,
)
from apps.core.services import next_code, next_sequence
from apps.core.tenancy import tenant_context
from apps.core.ui import csv_response, paginate
from apps.core.uploads import save_tenant_upload
from apps.project_management.forms import (
    EXPENSE_CATEGORIES,
    ApprovalSendForm,
    ChangeRequestForm,
    DashboardForm,
    ExpenseForm,
    HandoverForm,
    HealthCheckCompleteForm,
    HealthScheduleForm,
    MomForm,
    ProjectForm,
    ReportSaveForm,
    SlaContractForm,
    SlaTicketForm,
    SlaTicketUpdateForm,
    TaskForm,
    TeamMemberForm,
    TimeEntryForm,
    WIDGET_CHOICES,
)
from apps.project_management.pdf import gantt_pdf, mom_pdf, report_pdf
from apps.project_management.services import (
    DATASETS,
    STATUS_CHOICES,
    employee_label,
    employee_scorecards,
    ensure_feature_flags,
    expense_burn_alerts,
    field_employee_map,
    flag_enabled,
    issue_magic_link,
    log_audit,
    project_financials,
    project_health,
    query_csv_rows,
    resolve_magic_link,
    run_query,
    send_pm_email,
    task_summary,
)

ZERO = Decimal("0.00")
PAGE_SIZE = 15
PROJECT_STATUSES = Project._meta.get_field("status").choices
TASK_STATUSES = ProjectTask._meta.get_field("status").choices
TICKET_STATUSES = SlaTicket._meta.get_field("status").choices
TICKET_PRIORITIES = SlaTicket._meta.get_field("priority").choices


def money(value) -> Decimal:
    return (value or ZERO).quantize(Decimal("0.01"))


def _get_project(request, pk):
    """Fetch a project the signed-in user is allowed to see (PPM-safe)."""
    project = get_object_or_404(Project, pk=pk)
    if not can_access_project(request.user, project.id):
        raise Http404
    return project


def pm_flag_required(flag_key):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not flag_enabled(flag_key):
                messages.warning(
                    request,
                    "This PM module is not enabled in the current licence tier.",
                )
                return redirect("/projects/dashboard")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def _access(request):
    return get_access(request.user)


def _employees(ids):
    return field_employee_map(ids)


def _category_label(value):
    return (value or "Uncategorised").replace("_", " ").title()


def _user_names(ids):
    ids = {pk for pk in ids if pk}
    if not ids:
        return {}
    return {
        row.id: row.get_full_name()
        for row in User.objects.filter(id__in=ids)
    }


def _project_rows(projects):
    manager_ids = {project.manager_id for project in projects if project.manager_id}
    managers = _employees(manager_ids)
    rows = []
    for project in projects:
        financials = project_financials(project)
        summary = task_summary(project.id)
        health = project_health(project, financials, summary)
        project.manager_name = employee_label(managers.get(project.manager_id))
        rows.append(
            {
                "project": project,
                "financials": financials,
                "summary": summary,
                "health": health,
            }
        )
    return rows


def _audit_rows(logs):
    logs = list(logs)
    users = _user_names({row.user_id for row in logs})
    projects = {
        row.id: f"{row.project_code} · {row.project_name}"
        for row in Project.objects.filter(id__in={row.project_id for row in logs})
    }
    tasks = {
        row.id: row.task_name
        for row in ProjectTask.objects.filter(id__in={row.task_id for row in logs if row.task_id})
    }
    for row in logs:
        row.user_name = users.get(row.user_id, "System")
        row.project_label = projects.get(row.project_id, f"Project #{row.project_id}")
        row.task_name = tasks.get(row.task_id, "")
    return logs


def _task_names(tasks):
    employees = _employees({task.assigned_to for task in tasks if task.assigned_to})
    for task in tasks:
        task.assignee_name = employee_label(employees.get(task.assigned_to))
        today = timezone.localdate()
        task.is_overdue = bool(
            task.deadline_date and task.deadline_date < today and task.status != "complete"
        )
    return tasks


def _scope_approved(project_id, task_id):
    return ProjectChangeRequest.objects.filter(
        project_id=project_id, task_id=task_id, status="approved"
    ).exists()


def _get_my_employee(user):
    if getattr(user, "employee_id", None):
        employee = Employee.objects.filter(pk=user.employee_id).first()
        if employee:
            return employee
    if user.email:
        return Employee.objects.filter(email__iexact=user.email).first()
    return None


def _apply_task_completion(task, old_status, user_id):
    status = task.status
    if status == "complete" and old_status != "complete":
        task.completion_timestamp = timezone.now()
        task.completed_at = task.completion_timestamp
        task.completion_percent = Decimal("100.00")
    elif status != "complete":
        task.completion_timestamp = None
        task.completed_at = None
    if status in {"in_progress", "blocked", "in_qa"} and not task.started_at:
        task.started_at = timezone.now()


def _log_task_edit(task, old_values, user_id):
    fields = [
        ("task_name", "title"),
        ("assigned_to", "assignee"),
        ("start_date", "start_date"),
        ("deadline_date", "due_date"),
        ("estimated_hours", "estimated_hours"),
        ("priority", "priority"),
        ("is_scope_change", "scope_change"),
        ("completion_percent", "completion_percent"),
    ]
    for field, label in fields:
        old_value = old_values.get(field)
        new_value = getattr(task, field)
        if str(old_value) != str(new_value):
            log_audit(
                task.project_id, user_id, action="update", task_id=task.id,
                field_name=label, old_value=old_value, new_value=new_value,
            )


# ---------------------------------------------------------------------------
# Dashboard and portfolio
# ---------------------------------------------------------------------------
@login_required
@module_access_required("projects")
def dashboard(request):
    projects = list(visible_projects(request.user).order_by("-updated_at", "-id"))
    financials = {project.id: project_financials(project) for project in projects}
    summaries = {project.id: task_summary(project.id) for project in projects}

    total_contract = sum((financials[p.id]["contract_value"] for p in projects), ZERO)
    total_expenses = sum((financials[p.id]["approved_expenses"] for p in projects), ZERO)
    total_margin = sum((financials[p.id]["margin"] for p in projects), ZERO)
    active_projects = [p for p in projects if p.status == "in_execution"]
    status_counts = defaultdict(int)
    for project in projects:
        status_counts[project.status] += 1

    task_counts = defaultdict(int)
    for summary in summaries.values():
        for key, value in summary["counts"].items():
            task_counts[key] += value

    alerts = expense_burn_alerts(limit=5)
    managers = _employees({p.manager_id for p in projects if p.manager_id})
    for row in alerts:
        row["project"].manager_name = employee_label(
            managers.get(row["project"].manager_id)
        )

    recent_rows = []
    for project in projects[:5]:
        health = project_health(project, financials[project.id], summaries[project.id])
        project.manager_name = employee_label(managers.get(project.manager_id))
        recent_rows.append(
            {
                "project": project,
                "financials": financials[project.id],
                "summary": summaries[project.id],
                "health": health,
            }
        )

    today = timezone.localdate()
    upcoming = list(
        ProjectTask.objects.exclude(status="complete")
        .filter(deadline_date__gte=today, deadline_date__lte=today + timedelta(days=21))
        .order_by("deadline_date", "id")[:8]
    )
    _task_names(upcoming)
    project_names = {p.id: p.project_code for p in projects}
    for task in upcoming:
        task.project_code = project_names.get(task.project_id, "")

    open_tickets = SlaTicket.objects.exclude(status__in=["resolved", "closed"])
    ticket_stats = {
        "open": open_tickets.count(),
        "critical": open_tickets.filter(priority="critical").count(),
        "breached": open_tickets.filter(breached=True).count(),
        "contracts": SlaContract.objects.filter(status="active").count(),
    }
    upcoming_checks = SlaHealthCheck.objects.filter(status="scheduled").order_by("scheduled_date")[:4]

    recent_logs = _audit_rows(ProjectAuditLog.objects.all()[:8])
    pending_moms = ProjectMom.objects.filter(status="pending_approval").count()
    pending_crs = ProjectChangeRequest.objects.filter(status="pending_approval").count()

    return render(
        request,
        "project_management/dashboard.html",
        {
            "page_title": "Project Management - SORA ERP",
            "stats": {
                "projects": len(projects),
                "active": len(active_projects),
                "bidding": status_counts.get("bidding", 0),
                "support": status_counts.get("support_mode", 0),
                "contract_value": money(total_contract),
                "expenses": money(total_expenses),
                "margin": money(total_margin),
            },
            "status_counts": dict(status_counts),
            "task_counts": dict(task_counts),
            "alerts": alerts,
            "recent_rows": recent_rows,
            "upcoming": upcoming,
            "ticket_stats": ticket_stats,
            "upcoming_checks": upcoming_checks,
            "recent_logs": recent_logs,
            "pending_approvals": pending_moms + pending_crs,
            "pending_moms": pending_moms,
            "pending_crs": pending_crs,
            "can_create": _access(request).can("projects", "create"),
        },
    )


@login_required
@module_access_required("projects")
def portfolio(request):
    queryset = visible_projects(request.user).order_by("-updated_at", "-id")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    project_type = request.GET.get("type") or ""
    priority = request.GET.get("priority") or ""
    if query:
        queryset = queryset.filter(
            Q(project_code__icontains=query)
            | Q(project_name__icontains=query)
            | Q(client_name__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    if project_type:
        queryset = queryset.filter(project_type=project_type)
    if priority:
        queryset = queryset.filter(priority=priority)
    total_contract = queryset.aggregate(total=Sum("contract_value"))["total"] or ZERO
    page_obj = paginate(request, queryset, PAGE_SIZE)
    rows = _project_rows(list(page_obj.object_list))
    return render(
        request,
        "project_management/portfolio.html",
        {
            "page_title": "Project Portfolio - SORA ERP",
            "page_obj": page_obj,
            "rows": rows,
            "search_query": query,
            "status_filter": status,
            "type_filter": project_type,
            "priority_filter": priority,
            "status_choices": PROJECT_STATUSES,
            "type_choices": Project._meta.get_field("project_type").choices,
            "priority_choices": Project._meta.get_field("priority").choices,
            "total_contract": money(total_contract),
            "can_create": _access(request).can("projects", "create"),
        },
    )


@login_required
@module_access_required("projects")
def project_create(request):
    access = _access(request)
    if access.has_grant("projects") and access.module_scope("projects") != "tenant":
        messages.error(request, "Project-scoped access cannot create new projects.")
        return redirect("/projects/portfolio")
    if not access.can("projects", "create"):
        messages.error(request, "You do not have permission to create projects.")
        return redirect("/projects/portfolio")
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.project_code = (
                form.cleaned_data.get("project_code")
                or next_code("PRJ", "projects", "project_code", width=4)
            )
            project.budget_amount = form.cleaned_data["contract_value"]
            project.manager_id = form.cleaned_data["manager"].id if form.cleaned_data.get("manager") else None
            project.created_by = request.user.id
            project.created_at = timezone.now()
            if not project.baseline_locked_at:
                project.baseline_start_date = project.start_date
                project.baseline_end_date = project.end_date
            project.save()
            log_audit(
                project.id, request.user.id, action="project_created",
                note=f"Project {project.project_code} created.",
            )
            messages.success(request, f"Project {project.project_code} created.")
            return redirect(f"/projects/{project.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ProjectForm(initial={"status": "bidding", "project_type": "external_client"})
    return render(
        request,
        "project_management/project_form.html",
        {"page_title": "Create Project - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("projects")
def project_edit(request, pk):
    access = _access(request)
    project = _get_project(request, pk)
    if not access.can("projects", "edit"):
        messages.error(request, "You do not have permission to edit projects.")
        return redirect(f"/projects/{project.id}")
    old_values = {
        field: getattr(project, field)
        for field in [
            "project_name", "project_type", "client_name", "client_email", "contract_value",
            "allocated_expense_budget", "start_date", "end_date", "status", "priority",
            "manager_id", "description",
        ]
    }
    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            project = form.save(commit=False)
            project.budget_amount = form.cleaned_data["contract_value"]
            project.manager_id = form.cleaned_data["manager"].id if form.cleaned_data.get("manager") else None
            if not project.baseline_locked_at:
                project.baseline_start_date = project.start_date
                project.baseline_end_date = project.end_date
            project.save()
            for field, old in old_values.items():
                new = getattr(project, field)
                if str(old) != str(new):
                    log_audit(
                        project.id, request.user.id, action="update", field_name=field,
                        old_value=old, new_value=new,
                    )
            messages.success(request, "Project updated.")
            return redirect(f"/projects/{project.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ProjectForm(instance=project)
        form.fields["manager"].initial = project.manager_id
    return render(
        request,
        "project_management/project_form.html",
        {
            "page_title": f"Edit {project.project_code} - SORA ERP",
            "form": form,
            "project": project,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("projects")
def project_detail(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    financials = project_financials(project)
    summary = task_summary(project.id)
    health = project_health(project, financials, summary)
    manager = _employees({project.manager_id}).get(project.manager_id)

    tasks = list(ProjectTask.objects.filter(project_id=project.id).order_by("status", "id")[:12])
    _task_names(tasks)
    approved_scope = set(
        ProjectChangeRequest.objects.filter(
            project_id=project.id, status="approved", task_id__isnull=False
        ).values_list("task_id", flat=True)
    )
    for task in tasks:
        task.scope_approved = task.id in approved_scope

    team = list(ProjectTeam.objects.filter(project_id=project.id, left_at__isnull=True).order_by("id"))
    team_employees = _employees({member.employee_id for member in team})
    for member in team:
        member.employee = team_employees.get(member.employee_id)
        member.employee_name = employee_label(member.employee)

    moms = list(ProjectMom.objects.filter(project_id=project.id)[:5])
    change_requests = list(ProjectChangeRequest.objects.filter(project_id=project.id)[:5])
    expenses = list(ProjectExpense.objects.filter(project_id=project.id).order_by("-expense_date", "-id")[:6])
    expense_submitters = _user_names({row.submitted_by for row in expenses})
    for row in expenses:
        row.submitter_name = expense_submitters.get(row.submitted_by, "—")
        if row.receipt_path:
            row.receipt_url = row.receipt_path if row.receipt_path.startswith("http") else f"/uploads/{row.receipt_path}"
    audit_logs = _audit_rows(ProjectAuditLog.objects.filter(project_id=project.id)[:10])
    tickets = list(SlaTicket.objects.filter(project_id=project.id).order_by("-created_at")[:5])
    sla_contract = SlaContract.objects.filter(project_id=project.id, status="active").first()
    latest_uat = ProjectMagicLink.objects.filter(
        project_id=project.id, action_type="accept_uat"
    ).first()

    return render(
        request,
        "project_management/project_detail.html",
        {
            "page_title": f"{project.project_code} - SORA ERP",
            "project": project,
            "financials": financials,
            "summary": summary,
            "health": health,
            "manager": manager,
            "manager_name": employee_label(manager),
            "tasks": tasks,
            "team": team,
            "moms": moms,
            "change_requests": change_requests,
            "expenses": expenses,
            "audit_logs": audit_logs,
            "tickets": tickets,
            "sla_contract": sla_contract,
            "latest_uat": latest_uat,
            "approval_form": ApprovalSendForm(initial={"email": project.client_email or ""}),
            "can_edit": access.can("projects", "edit"),
            "can_create": access.can("projects", "create"),
            "can_approve": access.can("projects", "approve"),
            "can_delete": access.can("projects", "delete"),
        },
    )


@login_required
@module_access_required("projects")
@require_POST
def project_lock_baseline(request, pk):
    access = _access(request)
    project = _get_project(request, pk)
    if not access.can("projects", "approve"):
        messages.error(request, "You do not have permission to lock a baseline.")
        return redirect(f"/projects/{project.id}")
    if project.baseline_locked_at:
        messages.info(request, "The baseline is already locked.")
        return redirect(f"/projects/{project.id}")
    if not project.start_date or not project.end_date:
        messages.error(request, "Set planned start and end dates before locking the baseline.")
        return redirect(f"/projects/{project.id}")
    project.baseline_start_date = project.start_date
    project.baseline_end_date = project.end_date
    project.baseline_locked_at = timezone.now()
    project.status = "in_execution"
    project.save(
        update_fields=[
            "baseline_start_date", "baseline_end_date", "baseline_locked_at", "status", "updated_at",
        ]
    )
    log_audit(
        project.id, request.user.id, action="baseline_locked",
        field_name="baseline",
        new_value=f"{project.baseline_start_date} → {project.baseline_end_date}",
        note="Bid baseline locked and project moved to execution.",
    )
    messages.success(request, "Baseline locked. The project is now in execution.")
    return redirect(f"/projects/{project.id}")


# ---------------------------------------------------------------------------
# Tender / Gantt
# ---------------------------------------------------------------------------
@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_TENDER")
def tender(request):
    projects = list(visible_projects(request.user).filter(status="bidding").order_by("end_date", "id"))
    rows = _project_rows(projects)
    pipeline = sum((row["financials"]["contract_value"] for row in rows), ZERO)
    return render(
        request,
        "project_management/tender.html",
        {
            "page_title": "Tenders & Gantt - SORA ERP",
            "rows": rows,
            "pipeline": money(pipeline),
            "can_approve": _access(request).can("projects", "approve"),
        },
    )


def _gantt_payload(project, tasks):
    rows = []
    for task in tasks:
        start = task.start_date or project.start_date
        end = task.end_date or task.deadline_date or project.end_date
        if start and not end:
            end = start + timedelta(days=1)
        if end and not start:
            start = end - timedelta(days=1)
        rows.append({"task": task, "start": start, "end": end})

    dated = [row for row in rows if row["start"] and row["end"]]
    starts = [row["start"] for row in dated]
    ends = [row["end"] for row in dated]
    for value in (project.start_date, project.baseline_start_date):
        if value:
            starts.append(value)
    for value in (project.end_date, project.baseline_end_date):
        if value:
            ends.append(value)
    if not starts or not ends:
        today = timezone.localdate()
        starts, ends = [today], [today + timedelta(days=30)]
    range_start = min(starts) - timedelta(days=3)
    range_end = max(ends) + timedelta(days=3)
    total_days = max((range_end - range_start).days + 1, 1)

    months = []
    cursor = date(range_start.year, range_start.month, 1)
    while cursor <= range_end:
        next_month = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
        start = max(cursor, range_start)
        end = min(next_month - timedelta(days=1), range_end)
        left = (start - range_start).days / total_days * 100
        width = ((end - start).days + 1) / total_days * 100
        months.append({"label": cursor.strftime("%b %y"), "left": round(left, 3), "width": round(width, 3)})
        cursor = next_month

    for row in rows:
        if row["start"] and row["end"]:
            row["left"] = round((row["start"] - range_start).days / total_days * 100, 3)
            row["width"] = round(((row["end"] - row["start"]).days + 1) / total_days * 100, 3)
        else:
            row["left"] = 0
            row["width"] = 0

    baseline = None
    if project.baseline_start_date and project.baseline_end_date:
        baseline = {
            "left": round((project.baseline_start_date - range_start).days / total_days * 100, 3),
            "width": round(
                ((project.baseline_end_date - project.baseline_start_date).days + 1) / total_days * 100,
                3,
            ),
        }
    today_pct = None
    today = timezone.localdate()
    if range_start <= today <= range_end:
        today_pct = round((today - range_start).days / total_days * 100, 3)

    return {
        "rows": rows,
        "months": months,
        "total_days": total_days,
        "range_start": range_start,
        "range_end": range_end,
        "baseline": baseline,
        "today_pct": today_pct,
    }


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_TENDER")
def gantt(request, pk):
    project = _get_project(request, pk)
    tasks = _task_names(list(ProjectTask.objects.filter(project_id=project.id).order_by("start_date", "id")))
    payload = _gantt_payload(project, tasks)
    approval_map = {
        row["task_id"]: row["status"]
        for row in ProjectChangeRequest.objects.filter(
            project_id=project.id, task_id__isnull=False
        ).values("task_id", "status")
    }
    for row in payload["rows"]:
        row["task"].cr_status = approval_map.get(row["task"].id)
    return render(
        request,
        "project_management/gantt.html",
        {
            "page_title": f"Gantt - {project.project_code}",
            "project": project,
            **payload,
            "can_edit": _access(request).can("projects", "edit"),
            "can_approve": _access(request).can("projects", "approve"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_TENDER")
def gantt_download(request, pk):
    project = _get_project(request, pk)
    tasks = list(ProjectTask.objects.filter(project_id=project.id).order_by("start_date", "id"))
    content = gantt_pdf(project, tasks)
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="gantt-{project.project_code}.pdf"'
    return response


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_TENDER")
@require_POST
def task_reschedule(request, pk, task_id):
    access = _access(request)
    if not access.can("projects", "edit"):
        return JsonResponse({"error": "Permission denied."}, status=403)
    project = _get_project(request, pk)
    task = get_object_or_404(ProjectTask, pk=task_id, project_id=project.id)
    if not task.start_date and not task.end_date and not task.deadline_date:
        return JsonResponse({"error": "Set a start date before dragging this task."}, status=400)
    try:
        payload = json.loads(request.body or "{}")
        delta = int(payload.get("delta_days", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid drag payload."}, status=400)
    if abs(delta) > 3650:
        return JsonResponse({"error": "That move is too large."}, status=400)
    old = f"{task.start_date} → {task.deadline_date or task.end_date}"
    shift = timedelta(days=delta)
    if task.start_date:
        task.start_date = task.start_date + shift
    if task.deadline_date:
        task.deadline_date = task.deadline_date + shift
    if task.end_date:
        task.end_date = task.end_date + shift
    task.save(update_fields=["start_date", "deadline_date", "end_date", "updated_at"])
    log_audit(
        project.id, request.user.id, action="rescheduled", task_id=task.id,
        field_name="dates", old_value=old,
        new_value=f"{task.start_date} → {task.deadline_date or task.end_date}",
        note="Task moved on the interactive Gantt canvas.",
    )
    return JsonResponse(
        {
            "ok": True,
            "start": task.start_date.strftime("%d %b %Y") if task.start_date else "",
            "due": (task.deadline_date or task.end_date).strftime("%d %b %Y")
            if (task.deadline_date or task.end_date) else "",
        }
    )


# ---------------------------------------------------------------------------
# Tasks, kanban and my work
# ---------------------------------------------------------------------------
@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_TASKS")
def task_list(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    queryset = ProjectTask.objects.filter(project_id=project.id).order_by("status", "deadline_date", "id")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    assignee = request.GET.get("assignee") or ""
    if query:
        queryset = queryset.filter(Q(task_name__icontains=query) | Q(description__icontains=query))
    if status:
        queryset = queryset.filter(status=status)
    if assignee:
        queryset = queryset.filter(assigned_to=assignee)
    page_obj = paginate(request, queryset, PAGE_SIZE)
    tasks = _task_names(list(page_obj.object_list))
    approved = set(
        ProjectChangeRequest.objects.filter(
            project_id=project.id, status="approved", task_id__isnull=False
        ).values_list("task_id", flat=True)
    )
    for task in tasks:
        task.scope_approved = task.id in approved
    assignee_ids = set(
        ProjectTask.objects.filter(project_id=project.id, assigned_to__isnull=False).values_list(
            "assigned_to", flat=True
        )
    ) | set(
        ProjectTeam.objects.filter(project_id=project.id, left_at__isnull=True).values_list(
            "employee_id", flat=True
        )
    )
    return render(
        request,
        "project_management/task_list.html",
        {
            "page_title": f"Tasks - {project.project_code}",
            "project": project,
            "page_obj": page_obj,
            "tasks": tasks,
            "search_query": query,
            "status_filter": status,
            "assignee_filter": assignee,
            "status_choices": TASK_STATUSES,
            "assignees": _employees(assignee_ids),
            "can_create": access.can("projects", "create"),
            "can_edit": access.can("projects", "edit"),
        },
    )


def _save_task_form(request, project, task=None):
    access = _access(request)
    is_edit = task is not None
    if is_edit and not access.can("projects", "edit"):
        messages.error(request, "You do not have permission to edit tasks.")
        return redirect(f"/projects/{project.id}/tasks")
    if not is_edit and not access.can("projects", "create"):
        messages.error(request, "You do not have permission to create tasks.")
        return redirect(f"/projects/{project.id}/tasks")

    old_values = {}
    old_status = None
    if task:
        old_status = task.status
        old_values = {
            field: getattr(task, field)
            for field in [
                "task_name", "assigned_to", "start_date", "deadline_date", "estimated_hours",
                "priority", "is_scope_change", "completion_percent",
            ]
        }

    form = TaskForm(request.POST or None, instance=task, project=project)
    if request.method == "POST" and form.is_valid():
        candidate = form.save(commit=False)
        assignee = form.cleaned_data.get("assigned_to")
        candidate.assigned_to = assignee.id if assignee else None
        parent_task = form.cleaned_data.get("parent_task")
        candidate.parent_task_id = parent_task.id if parent_task else None
        new_status = candidate.status
        scope_flag = candidate.is_scope_change
        if is_edit:
            scope_flag = scope_flag or task.is_scope_change
        if new_status == "complete" and scope_flag and not _scope_approved(project.id, task.id if task else 0):
            form.add_error(
                None,
                "This is out-of-scope work. An approved Change Request linked to the task is required before it can be completed.",
            )
        else:
            with transaction.atomic():
                if not is_edit:
                    candidate.project_id = project.id
                    candidate.assigned_by = request.user.id
                    candidate.created_at = timezone.now()
                    highest = (
                        ProjectTask.objects.filter(project_id=project.id).aggregate(max=Max("board_order"))[
                            "max"
                        ]
                        or 0
                    )
                    candidate.board_order = highest + 1
                candidate.end_date = candidate.deadline_date
                _apply_task_completion(candidate, old_status, request.user.id)
                candidate.save()
                if not is_edit:
                    log_audit(
                        project.id, request.user.id, action="task_created", task_id=candidate.id,
                        field_name="task", new_value=candidate.task_name,
                        note="Task created.",
                    )
                else:
                    _log_task_edit(candidate, old_values, request.user.id)
                    if old_status != candidate.status:
                        log_audit(
                            project.id, request.user.id, action="status_change", task_id=candidate.id,
                            field_name="status", old_value=old_status, new_value=candidate.status,
                        )
            messages.success(request, f"Task {'updated' if is_edit else 'created'}.")
            return redirect(f"/projects/{project.id}/tasks")
        messages.error(request, "Please correct the errors below.")

    return render(
        request,
        "project_management/task_form.html",
        {
            "page_title": f"{'Edit' if is_edit else 'New'} Task - {project.project_code}",
            "form": form,
            "project": project,
            "task": task,
            "is_edit": is_edit,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_TASKS")
def task_create(request, pk):
    project = _get_project(request, pk)
    return _save_task_form(request, project)


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_TASKS")
def task_edit(request, pk, task_id):
    project = _get_project(request, pk)
    task = get_object_or_404(ProjectTask, pk=task_id, project_id=project.id)
    return _save_task_form(request, project, task)


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_KANBAN")
def board(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    tasks = _task_names(list(ProjectTask.objects.filter(project_id=project.id).order_by("board_order", "id")))
    approved = set(
        ProjectChangeRequest.objects.filter(
            project_id=project.id, status="approved", task_id__isnull=False
        ).values_list("task_id", flat=True)
    )
    for task in tasks:
        task.scope_approved = task.id in approved
    columns = []
    for value, label in TASK_STATUSES:
        columns.append(
            {
                "status": value,
                "label": label,
                "tasks": [task for task in tasks if task.status == value],
            }
        )
    return render(
        request,
        "project_management/board.html",
        {
            "page_title": f"Task Board - {project.project_code}",
            "project": project,
            "columns": columns,
            "can_edit": access.can("projects", "edit"),
            "can_create": access.can("projects", "create"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_KANBAN")
@require_POST
def board_move(request, pk):
    access = _access(request)
    if not access.can("projects", "edit"):
        return JsonResponse({"error": "Permission denied."}, status=403)
    project = _get_project(request, pk)
    try:
        payload = json.loads(request.body or "{}")
        task_id = int(payload.get("task_id"))
        status = payload.get("status")
        target_ids = [int(value) for value in payload.get("ordered_ids", [])]
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid board payload."}, status=400)
    if status not in dict(TASK_STATUSES):
        return JsonResponse({"error": "Invalid task status."}, status=400)
    task = get_object_or_404(ProjectTask, pk=task_id, project_id=project.id)
    if status == "complete" and task.is_scope_change and not _scope_approved(project.id, task.id):
        return JsonResponse(
            {
                "error": "Approved Change Request required. Out-of-scope tasks cannot be completed until the CR is approved."
            },
            status=409,
        )
    old_status = task.status
    with transaction.atomic():
        task.status = status
        _apply_task_completion(task, old_status, request.user.id)
        task.save()
        if target_ids:
            valid_ids = set(
                ProjectTask.objects.filter(project_id=project.id, id__in=target_ids).values_list(
                    "id", flat=True
                )
            )
            for index, row_id in enumerate([value for value in target_ids if value in valid_ids]):
                ProjectTask.objects.filter(pk=row_id).update(board_order=index)
        if old_status != status:
            log_audit(
                project.id, request.user.id, action="status_change", task_id=task.id,
                field_name="status", old_value=old_status, new_value=status,
                note="Moved on the Kanban board.",
            )
    return JsonResponse(
        {
            "ok": True,
            "status": status,
            "completion_percent": float(task.completion_percent or 0),
        }
    )


@login_required
@module_access_required("projects")
def my_tasks(request):
    employee = _get_my_employee(request.user)
    tasks = []
    projects = {}
    if employee:
        queryset = ProjectTask.objects.filter(assigned_to=employee.id).exclude(status="complete")
        status = request.GET.get("status") or ""
        if status:
            queryset = queryset.filter(status=status)
        tasks = _task_names(list(queryset.order_by("deadline_date", "id")))
        project_map = {
            row.id: row
            for row in Project.objects.filter(id__in={task.project_id for task in tasks})
        }
        for task in tasks:
            task.project = project_map.get(task.project_id)
    return render(
        request,
        "project_management/my_tasks.html",
        {
            "page_title": "My Work - SORA ERP",
            "employee": employee,
            "tasks": tasks,
            "status_filter": request.GET.get("status") or "",
            "status_choices": TASK_STATUSES,
        },
    )


# ---------------------------------------------------------------------------
# MOM Shield and change requests
# ---------------------------------------------------------------------------
def _mom_context(request, project, mom):
    links = list(
        ProjectMagicLink.objects.filter(
            project_id=project.id, action_type="approve_mom", target_entity_id=mom.id
        )
    )
    return {
        "project": project,
        "mom": mom,
        "links": links,
        "send_form": ApprovalSendForm(initial={"email": project.client_email or ""}),
        "can_approve": _access(request).can("projects", "approve"),
    }


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def moms(request, pk):
    project = _get_project(request, pk)
    rows = list(ProjectMom.objects.filter(project_id=project.id))
    return render(
        request,
        "project_management/moms.html",
        {
            "page_title": f"MOMs - {project.project_code}",
            "project": project,
            "moms": rows,
            "can_create": _access(request).can("projects", "create"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def mom_create(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    if not access.can("projects", "create"):
        messages.error(request, "You do not have permission to create MOMs.")
        return redirect(f"/projects/{project.id}/moms")
    if request.method == "POST":
        form = MomForm(request.POST)
        if form.is_valid():
            mom = form.save(commit=False)
            mom.project_id = project.id
            mom.created_by = request.user.id
            mom.created_at = timezone.now()
            mom.status = "draft"
            mom.save()
            log_audit(
                project.id, request.user.id, action="mom_created",
                note=f"MOM '{mom.title}' created.",
            )
            messages.success(request, "Minutes of meeting saved.")
            return redirect(f"/projects/{project.id}/moms/{mom.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = MomForm(initial={"title": f"Project review - {timezone.localdate():%d %b %Y}"})
    return render(
        request,
        "project_management/mom_form.html",
        {"page_title": "New MOM - SORA ERP", "project": project, "form": form, "is_edit": False},
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def mom_edit(request, pk, mom_id):
    project = _get_project(request, pk)
    mom = get_object_or_404(ProjectMom, pk=mom_id, project_id=project.id)
    if not _access(request).can("projects", "edit"):
        messages.error(request, "You do not have permission to edit MOMs.")
        return redirect(f"/projects/{project.id}/moms/{mom.id}")
    if request.method == "POST":
        form = MomForm(request.POST, instance=mom)
        if form.is_valid():
            form.save()
            log_audit(
                project.id, request.user.id, action="mom_updated",
                note=f"MOM '{mom.title}' updated.",
            )
            messages.success(request, "MOM updated.")
            return redirect(f"/projects/{project.id}/moms/{mom.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = MomForm(instance=mom)
    return render(
        request,
        "project_management/mom_form.html",
        {
            "page_title": "Edit MOM - SORA ERP", "project": project, "mom": mom,
            "form": form, "is_edit": True,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def mom_detail(request, pk, mom_id):
    project = _get_project(request, pk)
    mom = get_object_or_404(ProjectMom, pk=mom_id, project_id=project.id)
    return render(
        request,
        "project_management/mom_detail.html",
        {
            "page_title": f"{mom.title} - {project.project_code}",
            **_mom_context(request, project, mom),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def mom_pdf_download(request, pk, mom_id):
    project = _get_project(request, pk)
    mom = get_object_or_404(ProjectMom, pk=mom_id, project_id=project.id)
    content = mom_pdf(project, mom)
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="mom-{project.project_code}-{mom.id}.pdf"'
    )
    return response


def _approval_sent_page(request, project, title, action_label, approval_url, expires_at,
                        email_sent, email_error, back_url):
    return render(
        request,
        "project_management/approval_sent.html",
        {
            "page_title": "Approval Link Created - SORA ERP",
            "project": project,
            "title": title,
            "action_label": action_label,
            "approval_url": approval_url,
            "expires_at": expires_at,
            "email_sent": email_sent,
            "email_error": email_error,
            "back_url": back_url,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
@require_POST
def mom_send(request, pk, mom_id):
    project = _get_project(request, pk)
    mom = get_object_or_404(ProjectMom, pk=mom_id, project_id=project.id)
    if not _access(request).can("projects", "approve"):
        messages.error(request, "You do not have permission to issue client approvals.")
        return redirect(f"/projects/{project.id}/moms/{mom.id}")
    if mom.status == "approved":
        messages.info(request, "This MOM has already been approved.")
        return redirect(f"/projects/{project.id}/moms/{mom.id}")
    form = ApprovalSendForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid recipient email address.")
        return redirect(f"/projects/{project.id}/moms/{mom.id}")

    link, raw_token = issue_magic_link(
        project, "approve_mom", mom.id, form.cleaned_data["email"], request.user.id
    )
    approval_url = request.build_absolute_uri(
        reverse("project_management:approval", kwargs={"token": raw_token})
    )
    mom.status = "pending_approval"
    mom.save(update_fields=["status", "updated_at"])
    subject = f"Approval required: {mom.title} · {project.project_code}"
    intro = (
        f"Dear {project.client_name or 'Client'},\n\n"
        f"{form.cleaned_data.get('message') or 'Please review and approve the minutes of meeting below.'}\n\n"
        f"Project: {project.project_code} · {project.project_name}\n"
        f"Meeting date: {mom.meeting_date:%d %B %Y}\n\n"
        f"Review and approve online (no login required):\n{approval_url}\n\n"
        f"This single-use link expires on {timezone.localtime(link.expires_at):%d %b %Y %H:%M}.\n\n"
        "SORA ERP · Project Management"
    )
    attachment = (
        f"MOM-{project.project_code}-{mom.id}.pdf", mom_pdf(project, mom), "application/pdf"
    )
    sent, error = send_pm_email(subject, intro, form.cleaned_data["email"], [attachment])
    if sent:
        link.sent_at = timezone.now()
        link.save(update_fields=["sent_at"])
        messages.success(request, f"MOM approval emailed to {form.cleaned_data['email']}.")
    else:
        messages.warning(request, f"The link was created but the email was not sent: {error}")
    log_audit(
        project.id, request.user.id, action="mom_sent",
        note=f"MOM '{mom.title}' sent to {form.cleaned_data['email']}.",
    )
    return _approval_sent_page(
        request, project, mom.title, "MOM approval", approval_url, link.expires_at,
        sent, error, f"/projects/{project.id}/moms/{mom.id}",
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def change_requests(request, pk):
    project = _get_project(request, pk)
    rows = list(ProjectChangeRequest.objects.filter(project_id=project.id))
    tasks = {
        row.id: row.task_name
        for row in ProjectTask.objects.filter(id__in={row.task_id for row in rows if row.task_id})
    }
    for row in rows:
        row.task_name = tasks.get(row.task_id, "")
    return render(
        request,
        "project_management/change_requests.html",
        {
            "page_title": f"Change Requests - {project.project_code}",
            "project": project,
            "change_requests": rows,
            "can_create": _access(request).can("projects", "create"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def change_request_create(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    if not access.can("projects", "create"):
        messages.error(request, "You do not have permission to raise change requests.")
        return redirect(f"/projects/{project.id}/change-requests")
    if request.method == "POST":
        form = ChangeRequestForm(request.POST, project=project)
        if form.is_valid():
            cr = form.save(commit=False)
            cr.project_id = project.id
            linked_task = form.cleaned_data.get("task")
            cr.task_id = linked_task.id if linked_task else None
            cr.cr_number = next_code(
                f"CR-{timezone.localdate().year}", "pm_change_requests", "cr_number", width=4
            )
            cr.status = "draft"
            cr.requested_by = request.user.id
            cr.requested_at = timezone.now()
            cr.created_at = timezone.now()
            cr.save()
            if cr.task_id:
                task = ProjectTask.objects.filter(pk=cr.task_id).first()
                if task and not task.is_scope_change:
                    task.is_scope_change = True
                    task.save(update_fields=["is_scope_change", "updated_at"])
                    log_audit(
                        project.id, request.user.id, action="update", task_id=task.id,
                        field_name="scope_change", old_value="False", new_value="True",
                        note=f"Task flagged as out-of-scope by {cr.cr_number}.",
                    )
            log_audit(
                project.id, request.user.id, action="change_request_created",
                note=f"{cr.cr_number} raised: {cr.title}",
            )
            messages.success(request, f"Change request {cr.cr_number} created.")
            return redirect(f"/projects/{project.id}/change-requests/{cr.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ChangeRequestForm(project=project)
    return render(
        request,
        "project_management/change_request_form.html",
        {
            "page_title": "New Change Request - SORA ERP", "project": project,
            "form": form, "is_edit": False,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def change_request_detail(request, pk, cr_id):
    project = _get_project(request, pk)
    cr = get_object_or_404(ProjectChangeRequest, pk=cr_id, project_id=project.id)
    task = ProjectTask.objects.filter(pk=cr.task_id).first() if cr.task_id else None
    links = list(
        ProjectMagicLink.objects.filter(
            project_id=project.id,
            action_type="approve_change_request",
            target_entity_id=cr.id,
        )
    )
    return render(
        request,
        "project_management/change_request_detail.html",
        {
            "page_title": f"{cr.cr_number} - {project.project_code}",
            "project": project,
            "cr": cr,
            "task": task,
            "links": links,
            "send_form": ApprovalSendForm(initial={"email": project.client_email or ""}),
            "can_approve": _access(request).can("projects", "approve"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
@require_POST
def change_request_send(request, pk, cr_id):
    project = _get_project(request, pk)
    cr = get_object_or_404(ProjectChangeRequest, pk=cr_id, project_id=project.id)
    if not _access(request).can("projects", "approve"):
        messages.error(request, "You do not have permission to issue client approvals.")
        return redirect(f"/projects/{project.id}/change-requests/{cr.id}")
    if cr.status == "approved":
        messages.info(request, "This change request is already approved.")
        return redirect(f"/projects/{project.id}/change-requests/{cr.id}")
    form = ApprovalSendForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid recipient email address.")
        return redirect(f"/projects/{project.id}/change-requests/{cr.id}")

    link, raw_token = issue_magic_link(
        project, "approve_change_request", cr.id, form.cleaned_data["email"], request.user.id
    )
    approval_url = request.build_absolute_uri(
        reverse("project_management:approval", kwargs={"token": raw_token})
    )
    cr.status = "pending_approval"
    cr.save(update_fields=["status", "updated_at"])
    subject = f"Change Request approval: {cr.cr_number} · {project.project_code}"
    intro = (
        f"Dear {project.client_name or 'Client'},\n\n"
        f"{form.cleaned_data.get('message') or 'A change request has been raised against your project. Please review and approve or reject it.'}\n\n"
        f"Change request: {cr.cr_number} · {cr.title}\n"
        f"Project: {project.project_code} · {project.project_name}\n"
        f"Additional cost: {cr.cost_impact:,.2f}\n"
        f"Schedule impact: {cr.schedule_impact_days} day(s)\n\n"
        f"Review and decide online (no login required):\n{approval_url}\n\n"
        f"This single-use link expires on {timezone.localtime(link.expires_at):%d %b %Y %H:%M}.\n\n"
        "SORA ERP · Project Management"
    )
    sent, error = send_pm_email(subject, intro, form.cleaned_data["email"])
    if sent:
        link.sent_at = timezone.now()
        link.save(update_fields=["sent_at"])
        messages.success(request, f"Change request emailed to {form.cleaned_data['email']}.")
    else:
        messages.warning(request, f"The link was created but the email was not sent: {error}")
    log_audit(
        project.id, request.user.id, action="change_request_sent",
        note=f"{cr.cr_number} sent to {form.cleaned_data['email']}.",
    )
    return _approval_sent_page(
        request, project, f"{cr.cr_number} · {cr.title}", "Change request approval",
        approval_url, link.expires_at, sent, error,
        f"/projects/{project.id}/change-requests/{cr.id}",
    )


def _magic_link_rows(links):
    links = list(links)
    project_map = {
        row.id: row
        for row in Project.objects.filter(id__in={link.project_id for link in links})
    }
    mom_ids = {link.target_entity_id for link in links if link.action_type == "approve_mom"}
    cr_ids = {
        link.target_entity_id for link in links if link.action_type == "approve_change_request"
    }
    mom_titles = {
        row.id: row.title for row in ProjectMom.objects.filter(id__in=mom_ids)
    }
    cr_numbers = {
        row.id: row.cr_number for row in ProjectChangeRequest.objects.filter(id__in=cr_ids)
    }
    for link in links:
        link.project = project_map.get(link.project_id)
        if link.action_type == "approve_mom":
            link.entity_label = mom_titles.get(link.target_entity_id, "MOM")
        elif link.action_type == "approve_change_request":
            link.entity_label = cr_numbers.get(link.target_entity_id, "Change request")
        else:
            link.entity_label = "UAT acceptance"
    return links


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_MOM_SHIELD")
def approvals_hub(request):
    moms = list(
        ProjectMom.objects.filter(status__in=["pending_approval", "draft"]).order_by(
            "-meeting_date"
        )[:20]
    )
    crs = list(
        ProjectChangeRequest.objects.filter(
            status__in=["pending_approval", "draft"]
        ).order_by("-created_at")[:20]
    )
    project_map = {
        row.id: row
        for row in Project.objects.filter(
            id__in={row.project_id for row in moms} | {row.project_id for row in crs}
        )
    }
    for row in moms + crs:
        row.project = project_map.get(row.project_id)
    recent_links = _magic_link_rows(ProjectMagicLink.objects.all()[:15])
    return render(
        request,
        "project_management/approvals.html",
        {
            "page_title": "MOM & Approvals - SORA ERP",
            "moms": moms,
            "change_requests": crs,
            "recent_links": recent_links,
        },
    )


@login_required
@module_access_required("projects")
@require_POST
def project_send_uat(request, pk):
    if not _access(request).can("projects", "approve"):
        messages.error(request, "You do not have permission to request UAT acceptance.")
        return redirect(f"/projects/{pk}")
    project = _get_project(request, pk)
    if project.uat_accepted_at:
        messages.info(request, "UAT has already been accepted for this project.")
        return redirect(f"/projects/{project.id}")
    form = ApprovalSendForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid recipient email address.")
        return redirect(f"/projects/{project.id}")
    link, raw_token = issue_magic_link(
        project, "accept_uat", project.id, form.cleaned_data["email"], request.user.id
    )
    approval_url = request.build_absolute_uri(
        reverse("project_management:approval", kwargs={"token": raw_token})
    )
    subject = f"UAT acceptance: {project.project_code} · {project.project_name}"
    intro = (
        f"Dear {project.client_name or 'Client'},\n\n"
        f"{form.cleaned_data.get('message') or 'Please confirm user acceptance testing for the project below.'}\n\n"
        f"Project: {project.project_code} · {project.project_name}\n\n"
        f"Accept UAT online (no login required):\n{approval_url}\n\n"
        f"This single-use link expires on {timezone.localtime(link.expires_at):%d %b %Y %H:%M}.\n\n"
        "SORA ERP · Project Management"
    )
    sent, error = send_pm_email(subject, intro, form.cleaned_data["email"])
    if sent:
        link.sent_at = timezone.now()
        link.save(update_fields=["sent_at"])
        messages.success(request, f"UAT link emailed to {form.cleaned_data['email']}.")
    else:
        messages.warning(request, f"The link was created but the email was not sent: {error}")
    log_audit(
        project.id, request.user.id, action="uat_sent",
        note=f"UAT acceptance sent to {form.cleaned_data['email']}.",
    )
    return _approval_sent_page(
        request, project, f"UAT acceptance · {project.project_name}", "UAT acceptance",
        approval_url, link.expires_at, sent, error, f"/projects/{project.id}",
    )


def approval(request, token):
    link = resolve_magic_link(token)
    if not link:
        return render(
            request,
            "project_management/public_approval.html",
            {"invalid": True, "page_title": "Invalid Approval Link - SORA ERP"},
            status=404,
        )
    with tenant_context(link.tenant):
        return _approval_response(request, link)


def _approval_response(request, link):
    project = Project.objects.filter(pk=link.project_id).first()
    if not project:
        return render(
            request,
            "project_management/public_approval.html",
            {"invalid": True, "page_title": "Invalid Approval Link - SORA ERP"},
            status=404,
        )

    entity = None
    action_label = "Approval"
    if link.action_type == "approve_mom":
        entity = ProjectMom.objects.filter(
            pk=link.target_entity_id, project_id=project.id
        ).first()
        action_label = "Minutes of meeting"
    elif link.action_type == "approve_change_request":
        entity = ProjectChangeRequest.objects.filter(
            pk=link.target_entity_id, project_id=project.id
        ).first()
        action_label = "Change request"
    else:
        action_label = "UAT acceptance"

    if not entity and link.action_type != "accept_uat":
        return render(
            request,
            "project_management/public_approval.html",
            {"invalid": True, "page_title": "Invalid Approval Link - SORA ERP"},
            status=404,
        )

    if request.method == "POST" and link.is_active:
        decision = request.POST.get("decision")
        notes = (request.POST.get("notes") or "").strip()
        if decision not in {"approve", "reject"}:
            messages.error(request, "Choose approve or reject.")
        else:
            approved = decision == "approve"
            now = timezone.now()
            with transaction.atomic():
                if link.action_type == "approve_mom" and entity:
                    entity.status = "approved" if approved else "rejected"
                    entity.approved_by_name = link.recipient_email or "Client"
                    entity.approved_at = now
                    entity.rejection_reason = "" if approved else notes
                    entity.save()
                elif link.action_type == "approve_change_request" and entity:
                    entity.status = "approved" if approved else "rejected"
                    entity.decided_by_name = link.recipient_email or "Client"
                    entity.decided_at = now
                    entity.decision_notes = notes
                    entity.save()
                elif link.action_type == "accept_uat" and approved:
                    project.uat_accepted_at = now
                    project.uat_accepted_by = link.recipient_email or "Client"
                    project.save(
                        update_fields=["uat_accepted_at", "uat_accepted_by", "updated_at"]
                    )
                link.used_at = now
                link.decision = "approved" if approved else "rejected"
                link.decision_notes = notes
                link.decided_at = now
                link.decided_ip = (request.META.get("REMOTE_ADDR") or "")[:64]
                link.save()
                log_audit(
                    project.id, None,
                    action=f"{link.action_type}_{'approved' if approved else 'rejected'}",
                    task_id=entity.task_id
                    if link.action_type == "approve_change_request" and entity else None,
                    new_value=decision,
                    note=(
                        f"Client decision via magic link from "
                        f"{link.recipient_email or 'unknown email'}: {notes}"
                    ).strip(),
                )
            messages.success(request, "Your decision has been recorded.")
            return render(
                request,
                "project_management/public_approval.html",
                {
                    "page_title": "Decision Recorded - SORA ERP",
                    "project": project,
                    "link": link,
                    "entity": entity,
                    "action_label": action_label,
                    "done": True,
                    "expired": False,
                    "decision": link.decision,
                },
            )

    return render(
        request,
        "project_management/public_approval.html",
        {
            "page_title": f"{action_label} - {project.project_code}",
            "project": project,
            "link": link,
            "entity": entity,
            "action_label": action_label,
            "done": link.used_at is not None,
            "expired": link.is_expired,
            "decision": link.decision,
        },
    )


# ---------------------------------------------------------------------------
# Team and timesheets
# ---------------------------------------------------------------------------
@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_USERS")
def team(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    members = list(ProjectTeam.objects.filter(project_id=project.id).order_by("left_at", "id"))
    employees = _employees({member.employee_id for member in members})
    for member in members:
        member.employee = employees.get(member.employee_id)
        member.employee_name = employee_label(member.employee)
        member.is_active = member.left_at is None
    form = TeamMemberForm()
    if request.method == "POST":
        if not access.can("projects", "edit"):
            messages.error(request, "You do not have permission to manage the team.")
            return redirect(f"/projects/{project.id}/team")
        form = TeamMemberForm(request.POST)
        if form.is_valid():
            employee = form.cleaned_data["employee"]
            member, created = ProjectTeam.objects.get_or_create(
                project_id=project.id,
                employee_id=employee.id,
                defaults={
                    "role": form.cleaned_data.get("role") or "",
                    "allocation_percentage": form.cleaned_data["allocation_percentage"],
                    "hourly_rate": form.cleaned_data.get("hourly_rate"),
                    "joined_at": form.cleaned_data.get("joined_at") or timezone.localdate(),
                    "created_at": timezone.now(),
                },
            )
            if not created:
                member.role = form.cleaned_data.get("role") or member.role
                member.allocation_percentage = form.cleaned_data["allocation_percentage"]
                member.hourly_rate = form.cleaned_data.get("hourly_rate")
                member.left_at = None
                member.joined_at = form.cleaned_data.get("joined_at") or member.joined_at
                member.save()
            log_audit(
                project.id, request.user.id, action="team_member_added",
                note=(
                    f"{employee_label(employee)} joined the project team as "
                    f"{member.role or 'team member'}."
                ),
            )
            messages.success(
                request,
                f"{employee_label(employee)} {'added to' if created else 'reactivated on'} the team.",
            )
            return redirect(f"/projects/{project.id}/team")
        messages.error(request, "Please correct the team member details.")
    return render(
        request,
        "project_management/team.html",
        {
            "page_title": f"Team · {project.project_code} - SORA ERP",
            "project": project,
            "members": members,
            "form": form,
            "active_count": sum(1 for member in members if member.is_active),
            "can_edit": access.can("projects", "edit"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_USERS")
@require_POST
def team_member_remove(request, pk, member_id):
    project = _get_project(request, pk)
    member = get_object_or_404(ProjectTeam, pk=member_id, project_id=project.id)
    if not _access(request).can("projects", "edit"):
        messages.error(request, "You do not have permission to manage the team.")
        return redirect(f"/projects/{project.id}/team")
    if member.left_at:
        messages.info(request, "This team member has already been removed.")
        return redirect(f"/projects/{project.id}/team")
    employee = _employees({member.employee_id}).get(member.employee_id)
    member.left_at = timezone.localdate()
    member.save(update_fields=["left_at"])
    log_audit(
        project.id, request.user.id, action="team_member_removed",
        note=f"{employee_label(employee)} left the project team.",
    )
    messages.success(
        request,
        f"{employee_label(employee)} removed. Use the handover wizard to reassign their open tasks.",
    )
    return redirect(f"/projects/{project.id}/team")


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_USERS")
def timesheets(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    if request.method == "POST":
        if not access.can("projects", "edit"):
            messages.error(request, "You do not have permission to log time.")
            return redirect(f"/projects/{project.id}/timesheets")
        form = TimeEntryForm(request.POST, project=project)
        if form.is_valid():
            employee = form.cleaned_data["employee"]
            task = form.cleaned_data.get("task")
            hours = form.cleaned_data["hours"]
            rate = form.cleaned_data.get("hourly_rate")
            if rate is None:
                team_member = ProjectTeam.objects.filter(
                    project_id=project.id, employee_id=employee.id, left_at__isnull=True
                ).first()
                rate = team_member.hourly_rate if team_member and team_member.hourly_rate else None
            entry = ProjectTimeEntry.objects.create(
                project_id=project.id,
                task_id=task.id if task else None,
                employee_id=employee.id,
                entry_date=form.cleaned_data["entry_date"],
                hours=hours,
                description=form.cleaned_data.get("description") or "",
                billable=1 if form.cleaned_data.get("billable") else 0,
                hourly_rate=rate,
                total_amount=(hours * rate).quantize(Decimal("0.01")) if rate else None,
                status="pending",
                created_at=timezone.now(),
            )
            log_audit(
                project.id, request.user.id, action="time_logged", task_id=entry.task_id,
                note=f"{employee_label(employee)} logged {hours}h on {entry.entry_date}.",
            )
            messages.success(request, "Time entry submitted for approval.")
            return redirect(f"/projects/{project.id}/timesheets")
        messages.error(request, "Please correct the time entry.")
    else:
        form = TimeEntryForm(project=project)

    queryset = ProjectTimeEntry.objects.filter(project_id=project.id).order_by("-entry_date", "-id")
    status_filter = request.GET.get("status") or ""
    if status_filter in {"pending", "approved", "rejected"}:
        queryset = queryset.filter(status=status_filter)
    entries = list(queryset[:200])
    employees = _employees({entry.employee_id for entry in entries})
    tasks = {
        task.id: task.task_name
        for task in ProjectTask.objects.filter(id__in={entry.task_id for entry in entries if entry.task_id})
    }
    for entry in entries:
        entry.employee_name = employee_label(employees.get(entry.employee_id))
        entry.task_name = tasks.get(entry.task_id, "General project time")
    approved_hours = (
        ProjectTimeEntry.objects.filter(project_id=project.id, status="approved")
        .aggregate(total=Sum("hours"))["total"] or ZERO
    )
    pending_hours = (
        ProjectTimeEntry.objects.filter(project_id=project.id, status="pending")
        .aggregate(total=Sum("hours"))["total"] or ZERO
    )
    approved_cost = sum(
        (
            (entry.total_amount or (entry.hours or ZERO) * (entry.hourly_rate or ZERO))
            for entry in ProjectTimeEntry.objects.filter(project_id=project.id, status="approved")
        ),
        ZERO,
    )
    return render(
        request,
        "project_management/timesheets.html",
        {
            "page_title": f"Timesheets · {project.project_code} - SORA ERP",
            "project": project,
            "entries": entries,
            "form": form,
            "status_filter": status_filter,
            "approved_hours": approved_hours,
            "pending_hours": pending_hours,
            "approved_cost": money(approved_cost),
            "can_edit": access.can("projects", "edit"),
            "can_approve": access.can("projects", "approve"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_USERS")
@require_POST
def time_entry_decision(request, pk, entry_id):
    project = _get_project(request, pk)
    entry = get_object_or_404(ProjectTimeEntry, pk=entry_id, project_id=project.id)
    if not _access(request).can("projects", "approve"):
        messages.error(request, "You do not have permission to approve time entries.")
        return redirect(f"/projects/{project.id}/timesheets")
    action = request.POST.get("action")
    if action not in {"approve", "reject"}:
        messages.error(request, "Unknown time entry action.")
        return redirect(f"/projects/{project.id}/timesheets")
    if entry.status != "pending":
        messages.info(request, "This time entry has already been reviewed.")
        return redirect(f"/projects/{project.id}/timesheets")
    with transaction.atomic():
        entry.status = "approved" if action == "approve" else "rejected"
        if action == "approve":
            entry.approved_by = request.user.id
            entry.approved_at = timezone.now()
            if entry.total_amount is None and entry.hourly_rate:
                entry.total_amount = (entry.hours * entry.hourly_rate).quantize(Decimal("0.01"))
        entry.save()
        if entry.task_id:
            approved_hours = (
                ProjectTimeEntry.objects.filter(task_id=entry.task_id, status="approved")
                .aggregate(total=Sum("hours"))["total"] or ZERO
            )
            ProjectTask.objects.filter(pk=entry.task_id).update(actual_hours=approved_hours)
        log_audit(
            project.id, request.user.id,
            action=f"time_{'approved' if action == 'approve' else 'rejected'}",
            task_id=entry.task_id,
            note=f"{entry.hours}h on {entry.entry_date} {entry.status}.",
        )
    messages.success(request, f"Time entry {entry.status}.")
    return redirect(f"/projects/{project.id}/timesheets")


# ---------------------------------------------------------------------------
# Field expenses
# ---------------------------------------------------------------------------
def _receipt_url(path):
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return f"/uploads/{path}"


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_FIELD_EXPENSES")
def expenses(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    financials = project_financials(project)
    if request.method == "POST":
        if not access.can("projects", "create") and not access.can("projects", "edit"):
            messages.error(request, "You do not have permission to submit expenses.")
            return redirect(f"/projects/{project.id}/expenses")
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            receipt_path = ""
            receipt = form.cleaned_data.get("receipt")
            if receipt:
                receipt_path = save_tenant_upload("expenses", receipt)
            expense = ProjectExpense.objects.create(
                project_id=project.id,
                expense_date=form.cleaned_data["expense_date"],
                description=form.cleaned_data.get("description") or "",
                amount=form.cleaned_data["amount"],
                category=form.cleaned_data["category"],
                vendor_id=form.cleaned_data["vendor"].id if form.cleaned_data.get("vendor") else None,
                receipt_path=receipt_path,
                submitted_by=request.user.id,
                status="pending",
                created_at=timezone.now(),
            )
            log_audit(
                project.id, request.user.id, action="expense_submitted",
                new_value=str(expense.amount),
                note=f"{_category_label(expense.category)} expense of {expense.amount} submitted.",
            )
            messages.success(request, "Expense submitted for approval.")
            return redirect(f"/projects/{project.id}/expenses")
        messages.error(request, "Please correct the expense details.")
    else:
        form = ExpenseForm()

    queryset = ProjectExpense.objects.filter(project_id=project.id).order_by("-expense_date", "-id")
    status_filter = request.GET.get("status") or ""
    if status_filter in {"pending", "approved", "rejected"}:
        queryset = queryset.filter(status=status_filter)
    rows = list(queryset[:300])
    submitters = _user_names({row.submitted_by for row in rows})
    vendors = {
        row.id: row.company_name
        for row in Vendor.objects.filter(id__in={row.vendor_id for row in rows if row.vendor_id})
    }
    for row in rows:
        row.submitter_name = submitters.get(row.submitted_by, "—")
        row.vendor_name = vendors.get(row.vendor_id, "")
        row.receipt_url = _receipt_url(row.receipt_path)
    return render(
        request,
        "project_management/expenses.html",
        {
            "page_title": f"Field Expenses · {project.project_code} - SORA ERP",
            "project": project,
            "rows": rows,
            "form": form,
            "financials": financials,
            "status_filter": status_filter,
            "can_submit": access.can("projects", "create") or access.can("projects", "edit"),
            "can_approve": access.can("projects", "approve"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_FIELD_EXPENSES")
@require_POST
def expense_decision(request, pk, expense_id):
    project = _get_project(request, pk)
    expense = get_object_or_404(ProjectExpense, pk=expense_id, project_id=project.id)
    if not _access(request).can("projects", "approve"):
        messages.error(request, "You do not have permission to approve expenses.")
        return redirect(f"/projects/{project.id}/expenses")
    action = request.POST.get("action")
    if action not in {"approve", "reject"}:
        messages.error(request, "Unknown expense action.")
        return redirect(f"/projects/{project.id}/expenses")
    if expense.status != "pending":
        messages.info(request, "This expense has already been reviewed.")
        return redirect(f"/projects/{project.id}/expenses")
    expense.status = "approved" if action == "approve" else "rejected"
    expense.approved_by = request.user.id
    expense.save(update_fields=["status", "approved_by", "updated_at"])
    log_audit(
        project.id, request.user.id,
        action=f"expense_{'approved' if action == 'approve' else 'rejected'}",
        new_value=str(expense.amount),
        note=f"Expense #{expense.id} ({_category_label(expense.category)}) {expense.status}.",
    )
    if expense.status == "approved":
        financials = project_financials(project)
        if financials["expense_alert"]:
            messages.warning(
                request,
                f"Expense burn is now {financials['expense_burn_percent']:.0f}% of the allocated "
                "budget. Finance has been flagged via the 80% alert.",
            )
        else:
            messages.success(request, "Expense approved.")
    else:
        messages.success(request, "Expense rejected.")
    return redirect(f"/projects/{project.id}/expenses")


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_FIELD_EXPENSES")
def expenses_all(request):
    queryset = ProjectExpense.objects.all().order_by("-expense_date", "-id")
    project_filter = request.GET.get("project") or ""
    status_filter = request.GET.get("status") or ""
    category_filter = request.GET.get("category") or ""
    query = (request.GET.get("q") or "").strip()
    if project_filter.isdigit():
        queryset = queryset.filter(project_id=int(project_filter))
    if status_filter in {"pending", "approved", "rejected"}:
        queryset = queryset.filter(status=status_filter)
    if category_filter:
        queryset = queryset.filter(category=category_filter)
    if query:
        queryset = queryset.filter(Q(description__icontains=query) | Q(category__icontains=query))
    projects = {
        row.id: f"{row.project_code} · {row.project_name}"
        for row in Project.objects.all().only("id", "project_code", "project_name")
    }
    rows = list(queryset[:1000])
    submitters = _user_names({row.submitted_by for row in rows})
    for row in rows:
        row.submitter_name = submitters.get(row.submitted_by, "—")
        row.project_label = projects.get(row.project_id, f"Project #{row.project_id}")
        row.receipt_url = _receipt_url(row.receipt_path)
    if request.GET.get("export") == "csv":
        return csv_response(
            "field-expenses.csv",
            ["Date", "Project", "Category", "Description", "Vendor", "Amount", "Status", "Submitted by"],
            [
                [
                    row.expense_date,
                    row.project_label,
                    _category_label(row.category),
                    row.description or "",
                    row.vendor_id or "",
                    row.amount,
                    row.status,
                    row.submitter_name,
                ]
                for row in rows
            ],
        )
    page_obj = paginate(request, queryset, PAGE_SIZE)
    page_rows = list(page_obj.object_list)
    submitters = _user_names({row.submitted_by for row in page_rows})
    for row in page_rows:
        row.submitter_name = submitters.get(row.submitted_by, "—")
        row.project_label = projects.get(row.project_id, f"Project #{row.project_id}")
        row.receipt_url = _receipt_url(row.receipt_path)
    return render(
        request,
        "project_management/expenses_all.html",
        {
            "page_title": "Field Expenses - SORA ERP",
            "page_obj": page_obj,
            "rows": page_rows,
            "projects": projects,
            "project_filter": project_filter,
            "status_filter": status_filter,
            "category_filter": category_filter,
            "search_query": query,
            "category_choices": EXPENSE_CATEGORIES,
        },
    )


# ---------------------------------------------------------------------------
# Immutable audit log
# ---------------------------------------------------------------------------
@login_required
@module_access_required("projects")
def audit_log(request, pk=None):
    project = _get_project(request, pk) if pk else None
    queryset = ProjectAuditLog.objects.all()
    if project:
        queryset = queryset.filter(project_id=project.id)
    action_filter = request.GET.get("action") or ""
    query = (request.GET.get("q") or "").strip()
    date_from = request.GET.get("from") or ""
    date_to = request.GET.get("to") or ""
    if action_filter:
        queryset = queryset.filter(action=action_filter)
    if query:
        queryset = queryset.filter(
            Q(field_name__icontains=query) | Q(old_value__icontains=query)
            | Q(new_value__icontains=query) | Q(note__icontains=query)
        )
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    page_obj = paginate(request, queryset.order_by("-created_at", "-id"), PAGE_SIZE)
    rows = _audit_rows(page_obj.object_list)
    actions = list(
        ProjectAuditLog.objects.order_by("action").values_list("action", flat=True).distinct()
    )
    return render(
        request,
        "project_management/audit.html",
        {
            "page_title": "Audit Trail - SORA ERP",
            "project": project,
            "page_obj": page_obj,
            "rows": rows,
            "actions": actions,
            "action_filter": action_filter,
            "search_query": query,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


# ---------------------------------------------------------------------------
# Handover / offboarding
# ---------------------------------------------------------------------------
@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_USERS")
def handover_index(request):
    handovers = list(ProjectHandover.objects.all().order_by("-created_at", "-id")[:200])
    projects = {
        row.id: f"{row.project_code} · {row.project_name}"
        for row in Project.objects.filter(id__in={row.project_id for row in handovers})
    }
    employees = _employees(
        {row.departing_employee_id for row in handovers}
        | {row.incoming_employee_id for row in handovers}
    )
    for row in handovers:
        row.project_label = projects.get(row.project_id, f"Project #{row.project_id}")
        row.departing_name = employee_label(employees.get(row.departing_employee_id))
        row.incoming_name = employee_label(employees.get(row.incoming_employee_id))
    return render(
        request,
        "project_management/handover_index.html",
        {
            "page_title": "Project Handovers - SORA ERP",
            "handovers": handovers,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_USERS")
def handover_project(request, pk):
    project = _get_project(request, pk)
    access = _access(request)
    if not access.can("projects", "edit"):
        messages.error(request, "You do not have permission to initiate handovers.")
        return redirect(f"/projects/{project.id}")
    if request.method == "POST":
        form = HandoverForm(request.POST, project=project)
        if form.is_valid():
            departing = form.cleaned_data["departing_employee"]
            incoming = form.cleaned_data["incoming_employee"]
            task_ids = [int(value) for value in form.cleaned_data.get("task_ids") or []]
            now = timezone.now()
            with transaction.atomic():
                handover = ProjectHandover.objects.create(
                    project_id=project.id,
                    departing_employee_id=departing.id,
                    incoming_employee_id=incoming.id,
                    status="completed",
                    state_of_play=form.cleaned_data["state_of_play"],
                    asset_notes=form.cleaned_data.get("asset_notes") or "",
                    initiated_by=request.user.id,
                    initiated_at=now,
                    completed_at=now,
                    created_at=now,
                )
                tasks = list(ProjectTask.objects.filter(project_id=project.id, id__in=task_ids))
                for task in tasks:
                    task.assigned_to = incoming.id
                    task.assigned_by = request.user.id
                    task.save(update_fields=["assigned_to", "assigned_by", "updated_at"])
                    ProjectHandoverItem.objects.create(
                        handover_id=handover.id,
                        item_type="task",
                        reference=f"Task #{task.id} · {task.task_name}",
                        notes=f"Reassigned from {employee_label(departing)}",
                        to_employee_id=incoming.id,
                        created_at=now,
                    )
                if handover.asset_notes:
                    ProjectHandoverItem.objects.create(
                        handover_id=handover.id,
                        item_type="asset",
                        reference="Asset / repository notes",
                        notes=handover.asset_notes,
                        to_employee_id=incoming.id,
                        created_at=now,
                    )
                ProjectTeam.objects.get_or_create(
                    project_id=project.id,
                    employee_id=incoming.id,
                    defaults={
                        "role": "Team member",
                        "allocation_percentage": Decimal("100.00"),
                        "joined_at": timezone.localdate(),
                        "created_at": now,
                    },
                )
                ProjectTeam.objects.filter(
                    project_id=project.id,
                    employee_id=departing.id,
                    left_at__isnull=True,
                ).update(left_at=timezone.localdate())
                log_audit(
                    project.id, request.user.id, action="handover_completed",
                    new_value=f"{employee_label(departing)} → {employee_label(incoming)}",
                    note=(
                        f"{len(tasks)} open task(s) reassigned with a state-of-play memo."
                    ),
                )
            messages.success(
                request,
                f"Handover complete. {len(tasks)} task(s) reassigned to {employee_label(incoming)}.",
            )
            return redirect(f"/projects/{project.id}/handovers/{handover.id}")
        messages.error(request, "Complete the handover requirements below.")
    else:
        form = HandoverForm(project=project)
    open_tasks = list(
        ProjectTask.objects.filter(project_id=project.id)
        .exclude(status="complete")
        .order_by("status", "deadline_date")
    )
    _task_names(open_tasks)
    team = list(ProjectTeam.objects.filter(project_id=project.id, left_at__isnull=True))
    team_employees = _employees({member.employee_id for member in team})
    for member in team:
        member.employee_name = employee_label(team_employees.get(member.employee_id))
    return render(
        request,
        "project_management/handover_form.html",
        {
            "page_title": f"Handover · {project.project_code} - SORA ERP",
            "project": project,
            "form": form,
            "open_tasks": open_tasks,
            "team": team,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_USERS")
def handover_detail(request, handover_id):
    handover = get_object_or_404(ProjectHandover, pk=handover_id)
    project = get_object_or_404(Project, pk=handover.project_id)
    items = list(ProjectHandoverItem.objects.filter(handover_id=handover.id).order_by("id"))
    employees = _employees({handover.departing_employee_id, handover.incoming_employee_id})
    for item in items:
        item.to_name = employee_label(employees.get(item.to_employee_id))
    return render(
        request,
        "project_management/handover_detail.html",
        {
            "page_title": f"Handover #{handover.id} - SORA ERP",
            "handover": handover,
            "project": project,
            "items": items,
            "departing_name": employee_label(employees.get(handover.departing_employee_id)),
            "incoming_name": employee_label(employees.get(handover.incoming_employee_id)),
        },
    )


# ---------------------------------------------------------------------------
# Resource scorecards
# ---------------------------------------------------------------------------
@login_required
@module_access_required("projects")
@pm_flag_required("FEATURE_USERS")
def scorecards(request):
    rows = employee_scorecards()
    if request.GET.get("export") == "csv":
        return csv_response(
            "pm-scorecards.csv",
            [
                "Employee", "Job title", "Tasks", "Completed", "Completion %",
                "On-time %", "Overdue", "Blocked", "Avg blocker hours", "Score",
            ],
            [
                [
                    row["name"], row["job_title"], row["total"], row["completed"],
                    row["completion_rate"], row["on_time_rate"], row["overdue"],
                    row["blocked"], row["average_blocker_hours"], row["score"],
                ]
                for row in rows
            ],
        )
    average = round(sum(row["score"] for row in rows) / len(rows), 1) if rows else 0
    return render(
        request,
        "project_management/scorecards.html",
        {
            "page_title": "Resource Scorecards - SORA ERP",
            "rows": rows,
            "average_score": average,
            "top_performer": rows[0] if rows else None,
            "at_risk": [row for row in rows if row["score"] < 50][:5],
        },
    )


# ---------------------------------------------------------------------------
# SLA / AMC hub
# ---------------------------------------------------------------------------
SLA_PRIORITY_FACTOR = {"critical": 0.25, "high": 0.5, "medium": 1.0, "low": 1.5}


def _sla_due_at(contract, priority, start=None):
    factor = SLA_PRIORITY_FACTOR.get(priority, 1.0)
    hours = max(1, round((contract.resolution_target_hours or 48) * factor))
    return (start or timezone.now()) + timedelta(hours=hours)


def _mark_ticket_breaches():
    now = timezone.now()
    SlaTicket.objects.filter(
        breached=False, sla_due_at__lt=now
    ).exclude(status__in=["resolved", "closed"]).update(breached=True)


def _contract_rows(contracts):
    contracts = list(contracts)
    counts = {}
    for row in SlaTicket.objects.filter(
        contract_id__in=[contract.id for contract in contracts]
    ).exclude(status__in=["resolved", "closed"]).values("contract_id").annotate(total=Count("id")):
        counts[row["contract_id"]] = row["total"]
    for contract in contracts:
        contract.open_tickets = counts.get(contract.id, 0)
    return contracts


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
def sla_hub(request):
    _mark_ticket_breaches()
    contracts = _contract_rows(SlaContract.objects.all().order_by("status", "-start_date"))
    open_tickets = SlaTicket.objects.exclude(status__in=["resolved", "closed"])
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ticket_stats = {
        "open": open_tickets.count(),
        "breached": SlaTicket.objects.filter(breached=True).exclude(
            status__in=["resolved", "closed"]
        ).count(),
        "resolved_month": SlaTicket.objects.filter(
            resolved_at__gte=month_start, status__in=["resolved", "closed"]
        ).count(),
        "critical": open_tickets.filter(priority="critical").count(),
    }
    upcoming_checks = list(
        SlaHealthCheck.objects.filter(status="scheduled").order_by("scheduled_date", "id")[:8]
    )
    contract_numbers = {contract.id: contract.contract_number for contract in contracts}
    for check in upcoming_checks:
        check.contract_number = contract_numbers.get(check.contract_id, f"Contract #{check.contract_id}")
    recent_tickets = list(SlaTicket.objects.all().order_by("-created_at", "-id")[:8])
    for ticket in recent_tickets:
        ticket.contract_number = contract_numbers.get(ticket.contract_id, f"Contract #{ticket.contract_id}")
    active_contracts = [contract for contract in contracts if contract.status == "active"]
    monthly_value = sum((contract.monthly_value or ZERO for contract in active_contracts), ZERO)
    return render(
        request,
        "project_management/sla_hub.html",
        {
            "page_title": "SLA & Support Hub - SORA ERP",
            "contracts": contracts,
            "active_count": len(active_contracts),
            "monthly_value": money(monthly_value),
            "ticket_stats": ticket_stats,
            "upcoming_checks": upcoming_checks,
            "recent_tickets": recent_tickets,
            "health_enabled": flag_enabled("MODULE_HEALTH_CHECKS"),
            "can_create": _access(request).can("projects", "create"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
def sla_contract_create(request):
    access = _access(request)
    if not access.can("projects", "create"):
        messages.error(request, "You do not have permission to create SLA contracts.")
        return redirect("/projects/sla")
    if request.method == "POST":
        form = SlaContractForm(request.POST)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.contract_number = (
                form.cleaned_data.get("contract_number")
                or next_code("SLA", "pm_sla_contracts", "contract_number", width=4)
            )
            contract.project_id = (
                form.cleaned_data["project"].id if form.cleaned_data.get("project") else None
            )
            contract.created_by = request.user.id
            contract.created_at = timezone.now()
            contract.save()
            if contract.project_id:
                log_audit(
                    contract.project_id, request.user.id, action="sla_contract_created",
                    note=f"SLA contract {contract.contract_number} created.",
                )
            messages.success(request, f"SLA contract {contract.contract_number} created.")
            return redirect(f"/projects/sla/contracts/{contract.id}")
        messages.error(request, "Please correct the SLA contract details.")
    else:
        form = SlaContractForm(initial={"status": "active"})
    return render(
        request,
        "project_management/sla_contract_form.html",
        {
            "page_title": "New SLA Contract - SORA ERP",
            "form": form,
            "is_edit": False,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
def sla_contract_edit(request, contract_id):
    contract = get_object_or_404(SlaContract, pk=contract_id)
    if not _access(request).can("projects", "edit"):
        messages.error(request, "You do not have permission to edit SLA contracts.")
        return redirect(f"/projects/sla/contracts/{contract.id}")
    if request.method == "POST":
        form = SlaContractForm(request.POST, instance=contract)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.project_id = (
                form.cleaned_data["project"].id if form.cleaned_data.get("project") else None
            )
            contract.save()
            if contract.project_id:
                log_audit(
                    contract.project_id, request.user.id, action="sla_contract_updated",
                    note=f"SLA contract {contract.contract_number} updated.",
                )
            messages.success(request, "SLA contract updated.")
            return redirect(f"/projects/sla/contracts/{contract.id}")
        messages.error(request, "Please correct the SLA contract details.")
    else:
        form = SlaContractForm(instance=contract)
        form.fields["project"].initial = contract.project_id
    return render(
        request,
        "project_management/sla_contract_form.html",
        {
            "page_title": f"Edit {contract.contract_number} - SORA ERP",
            "form": form,
            "contract": contract,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
def sla_contract_detail(request, contract_id):
    contract = get_object_or_404(SlaContract, pk=contract_id)
    project = Project.objects.filter(pk=contract.project_id).first() if contract.project_id else None
    tickets = list(SlaTicket.objects.filter(contract_id=contract.id).order_by("-created_at", "-id"))
    assignees = _employees({ticket.assigned_to for ticket in tickets if ticket.assigned_to})
    for ticket in tickets:
        ticket.assignee_name = employee_label(assignees.get(ticket.assigned_to))
    checks = list(SlaHealthCheck.objects.filter(contract_id=contract.id).order_by("scheduled_date"))
    performed = _employees({check.performed_by for check in checks if check.performed_by})
    for check in checks:
        check.performed_name = employee_label(performed.get(check.performed_by))
    open_count = sum(1 for ticket in tickets if ticket.status not in {"resolved", "closed"})
    breached_count = sum(1 for ticket in tickets if ticket.breached)
    return render(
        request,
        "project_management/sla_contract_detail.html",
        {
            "page_title": f"{contract.contract_number} - SORA ERP",
            "contract": contract,
            "project": project,
            "tickets": tickets,
            "checks": checks,
            "open_count": open_count,
            "breached_count": breached_count,
            "health_enabled": flag_enabled("MODULE_HEALTH_CHECKS"),
            "can_edit": _access(request).can("projects", "edit"),
            "health_form": HealthScheduleForm(),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
@require_POST
def sla_contract_convert(request, pk):
    project = _get_project(request, pk)
    if not _access(request).can("projects", "approve"):
        messages.error(request, "You do not have permission to convert projects into support.")
        return redirect(f"/projects/{project.id}")
    if SlaContract.objects.filter(project_id=project.id, status="active").exists():
        messages.info(request, "This project already has an active SLA contract.")
        return redirect(f"/projects/{project.id}")
    today = timezone.localdate()
    contract = SlaContract.objects.create(
        project_id=project.id,
        contract_number=next_code("SLA", "pm_sla_contracts", "contract_number", width=4),
        title=f"{project.project_name} — Managed Support",
        client_name=project.client_name or project.project_name,
        start_date=today,
        end_date=today + timedelta(days=365),
        support_tier="priority" if project.priority in {"high", "critical"} else "standard",
        monthly_value=ZERO,
        response_target_hours=8,
        resolution_target_hours=48,
        status="active",
        auto_created_from_project=True,
        created_by=request.user.id,
        created_at=timezone.now(),
    )
    project.status = "support_mode"
    project.save(update_fields=["status", "updated_at"])
    log_audit(
        project.id, request.user.id, action="project_support_mode",
        new_value=contract.contract_number,
        note="Project converted to support mode with an SLA contract.",
    )
    messages.success(
        request, f"Project moved to support mode. Contract {contract.contract_number} created."
    )
    return redirect(f"/projects/sla/contracts/{contract.id}")


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
def sla_tickets(request):
    _mark_ticket_breaches()
    queryset = SlaTicket.objects.all().order_by("-created_at", "-id")
    status_filter = request.GET.get("status") or ""
    priority_filter = request.GET.get("priority") or ""
    contract_filter = request.GET.get("contract") or ""
    query = (request.GET.get("q") or "").strip()
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if priority_filter:
        queryset = queryset.filter(priority=priority_filter)
    if contract_filter.isdigit():
        queryset = queryset.filter(contract_id=int(contract_filter))
    if query:
        queryset = queryset.filter(
            Q(ticket_number__icontains=query) | Q(title__icontains=query)
            | Q(description__icontains=query)
        )
    page_obj = paginate(request, queryset, PAGE_SIZE)
    rows = list(page_obj.object_list)
    contracts = {
        row.id: row.contract_number
        for row in SlaContract.objects.filter(id__in={row.contract_id for row in rows})
    }
    projects = {
        row.id: row.project_code
        for row in Project.objects.filter(id__in={row.project_id for row in rows if row.project_id})
    }
    assignees = _employees({row.assigned_to for row in rows if row.assigned_to})
    for row in rows:
        row.contract_number = contracts.get(row.contract_id, f"Contract #{row.contract_id}")
        row.project_code = projects.get(row.project_id, "")
        row.assignee_name = employee_label(assignees.get(row.assigned_to))
        row.is_overdue = bool(
            row.sla_due_at and row.sla_due_at < timezone.now()
            and row.status not in {"resolved", "closed"}
        )
    return render(
        request,
        "project_management/sla_tickets.html",
        {
            "page_title": "SLA Tickets - SORA ERP",
            "page_obj": page_obj,
            "rows": rows,
            "contracts": SlaContract.objects.all().order_by("contract_number"),
            "status_filter": status_filter,
            "priority_filter": priority_filter,
            "contract_filter": contract_filter,
            "search_query": query,
            "status_choices": TICKET_STATUSES,
            "priority_choices": TICKET_PRIORITIES,
            "can_create": _access(request).can("projects", "create"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
def sla_ticket_create(request):
    access = _access(request)
    if not access.can("projects", "create"):
        messages.error(request, "You do not have permission to create tickets.")
        return redirect("/projects/sla/tickets")
    initial = {}
    if request.GET.get("contract", "").isdigit():
        contract = SlaContract.objects.filter(pk=int(request.GET["contract"])).first()
        if contract:
            initial = {"contract": contract, "project": contract.project_id}
    if request.method == "POST":
        form = SlaTicketForm(request.POST)
        if form.is_valid():
            contract = form.cleaned_data["contract"]
            priority = form.cleaned_data["priority"]
            now = timezone.now()
            ticket = form.save(commit=False)
            assignee = form.cleaned_data.get("assigned_to")
            ticket.assigned_to = assignee.id if assignee else None
            ticket.contract_id = contract.id
            ticket.project_id = (
                form.cleaned_data["project"].id if form.cleaned_data.get("project") else None
            )
            ticket.ticket_number = next_sequence("TKT", "pm_sla_tickets", "ticket_number")
            ticket.sla_due_at = _sla_due_at(contract, priority, now)
            ticket.created_by = request.user.id
            ticket.created_at = now
            if ticket.assigned_to:
                ticket.first_response_at = now
            ticket.save()
            if ticket.project_id:
                log_audit(
                    ticket.project_id, request.user.id, action="ticket_created",
                    new_value=ticket.ticket_number,
                    note=f"SLA ticket “{ticket.title}” logged under {contract.contract_number}.",
                )
            messages.success(request, f"Ticket {ticket.ticket_number} created.")
            return redirect(f"/projects/sla/tickets/{ticket.id}")
        messages.error(request, "Please correct the ticket details.")
    else:
        form = SlaTicketForm(initial=initial)
    return render(
        request,
        "project_management/sla_ticket_form.html",
        {
            "page_title": "New SLA Ticket - SORA ERP",
            "form": form,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
def sla_ticket_detail(request, ticket_id):
    _mark_ticket_breaches()
    ticket = get_object_or_404(SlaTicket, pk=ticket_id)
    contract = SlaContract.objects.filter(pk=ticket.contract_id).first()
    project = Project.objects.filter(pk=ticket.project_id).first() if ticket.project_id else None
    source_task = ProjectTask.objects.filter(pk=ticket.source_task_id).first() if ticket.source_task_id else None
    assignees = _employees({ticket.assigned_to}) if ticket.assigned_to else {}
    ticket.assignee_name = employee_label(assignees.get(ticket.assigned_to))
    return render(
        request,
        "project_management/sla_ticket_detail.html",
        {
            "page_title": f"{ticket.ticket_number} - SORA ERP",
            "ticket": ticket,
            "contract": contract,
            "project": project,
            "source_task": source_task,
            "update_form": SlaTicketUpdateForm(
                initial={"status": ticket.status, "assigned_to": ticket.assigned_to}
            ),
            "can_edit": _access(request).can("projects", "edit"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
@require_POST
def sla_ticket_update(request, ticket_id):
    ticket = get_object_or_404(SlaTicket, pk=ticket_id)
    if not _access(request).can("projects", "edit"):
        messages.error(request, "You do not have permission to update tickets.")
        return redirect(f"/projects/sla/tickets/{ticket.id}")
    form = SlaTicketUpdateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the ticket update.")
        return redirect(f"/projects/sla/tickets/{ticket.id}")
    now = timezone.now()
    old_status = ticket.status
    ticket.status = form.cleaned_data["status"]
    ticket.assigned_to = (
        form.cleaned_data["assigned_to"].id if form.cleaned_data.get("assigned_to") else None
    )
    if ticket.status != old_status and ticket.first_response_at is None:
        ticket.first_response_at = now
    if ticket.status in {"resolved", "closed"}:
        ticket.resolved_at = ticket.resolved_at or now
    else:
        ticket.resolved_at = None
    if ticket.sla_due_at and ticket.sla_due_at < now and ticket.status not in {"resolved", "closed"}:
        ticket.breached = True
    ticket.save()
    if ticket.project_id:
        log_audit(
            ticket.project_id, request.user.id, action="ticket_updated",
            field_name="status", old_value=old_status, new_value=ticket.status,
            note=(form.cleaned_data.get("note") or f"Ticket {ticket.ticket_number} updated."),
        )
    messages.success(request, "Ticket updated.")
    return redirect(f"/projects/sla/tickets/{ticket.id}")


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_SLA_HUB")
@require_POST
def sla_ticket_from_task(request, task_id):
    task = get_object_or_404(ProjectTask, pk=task_id)
    project = get_object_or_404(Project, pk=task.project_id)
    if not _access(request).can("projects", "create"):
        messages.error(request, "You do not have permission to convert tasks into tickets.")
        return redirect(f"/projects/{project.id}/tasks")
    contract = SlaContract.objects.filter(project_id=project.id, status="active").first()
    if not contract:
        messages.error(
            request,
            "Create an active SLA contract for this project before converting unresolved tasks.",
        )
        return redirect(f"/projects/{project.id}")
    if SlaTicket.objects.filter(source_task_id=task.id).exists():
        messages.info(request, "This task already has a linked SLA ticket.")
        existing = SlaTicket.objects.filter(source_task_id=task.id).first()
        return redirect(f"/projects/sla/tickets/{existing.id}")
    now = timezone.now()
    priority = "high" if task.priority == "high" else "medium"
    ticket = SlaTicket.objects.create(
        contract_id=contract.id,
        project_id=project.id,
        source_task_id=task.id,
        ticket_number=next_sequence("TKT", "pm_sla_tickets", "ticket_number"),
        title=f"{task.task_name} (project carry-over)",
        description=(
            f"Converted from project task #{task.id} at handover. "
            f"Original status: {task.get_status_display()}."
        ),
        priority=priority,
        status="open",
        sla_due_at=_sla_due_at(contract, priority, now),
        created_by=request.user.id,
        created_at=now,
    )
    log_audit(
        project.id, request.user.id, action="task_to_ticket",
        task_id=task.id, new_value=ticket.ticket_number,
        note=f"Unresolved task converted to SLA ticket {ticket.ticket_number}.",
    )
    messages.success(request, f"Task converted to ticket {ticket.ticket_number}.")
    return redirect(f"/projects/sla/tickets/{ticket.id}")


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_HEALTH_CHECKS")
def health_checks(request):
    queryset = SlaHealthCheck.objects.all().order_by("scheduled_date", "id")
    status_filter = request.GET.get("status") or ""
    contract_filter = request.GET.get("contract") or ""
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if contract_filter.isdigit():
        queryset = queryset.filter(contract_id=int(contract_filter))
    page_obj = paginate(request, queryset, PAGE_SIZE)
    rows = list(page_obj.object_list)
    contracts = {
        row.id: row.contract_number
        for row in SlaContract.objects.filter(id__in={row.contract_id for row in rows})
    }
    employees = _employees({row.performed_by for row in rows if row.performed_by})
    today = timezone.localdate()
    for row in rows:
        row.contract_number = contracts.get(row.contract_id, f"Contract #{row.contract_id}")
        row.performed_name = employee_label(employees.get(row.performed_by))
        row.is_overdue = row.status == "scheduled" and row.scheduled_date < today
    return render(
        request,
        "project_management/health_checks.html",
        {
            "page_title": "Preventive Health Checks - SORA ERP",
            "page_obj": page_obj,
            "rows": rows,
            "contracts": SlaContract.objects.all().order_by("contract_number"),
            "status_filter": status_filter,
            "contract_filter": contract_filter,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_HEALTH_CHECKS")
def health_check_schedule(request, contract_id):
    contract = get_object_or_404(SlaContract, pk=contract_id)
    if not _access(request).can("projects", "edit"):
        messages.error(request, "You do not have permission to schedule health checks.")
        return redirect(f"/projects/sla/contracts/{contract.id}")
    if request.method == "POST":
        form = HealthScheduleForm(request.POST)
        if form.is_valid():
            start = form.cleaned_data["start_date"]
            frequency = form.cleaned_data["frequency"]
            occurrences = form.cleaned_data["occurrences"]
            step = 1 if frequency == "monthly" else 3
            created = 0
            for index in range(occurrences):
                SlaHealthCheck.objects.create(
                    contract_id=contract.id,
                    project_id=contract.project_id,
                    scheduled_date=_add_months(start, index * step),
                    check_type="preventive",
                    status="scheduled",
                    created_at=timezone.now(),
                )
                created += 1
            if contract.project_id:
                log_audit(
                    contract.project_id, request.user.id, action="health_checks_scheduled",
                    new_value=str(created),
                    note=f"{created} {frequency} preventive visits scheduled for {contract.contract_number}.",
                )
            messages.success(request, f"{created} health check visit(s) scheduled.")
            return redirect(f"/projects/sla/contracts/{contract.id}")
        messages.error(request, "Please correct the schedule details.")
    else:
        form = HealthScheduleForm()
    return render(
        request,
        "project_management/health_check_form.html",
        {
            "page_title": f"Schedule Health Checks · {contract.contract_number}",
            "contract": contract,
            "form": form,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_HEALTH_CHECKS")
def health_check_complete(request, check_id):
    check = get_object_or_404(SlaHealthCheck, pk=check_id)
    contract = get_object_or_404(SlaContract, pk=check.contract_id)
    if not _access(request).can("projects", "edit"):
        messages.error(request, "You do not have permission to complete health checks.")
        return redirect(f"/projects/sla/contracts/{contract.id}")
    if check.status == "completed":
        messages.info(request, "This visit is already complete.")
        return redirect(f"/projects/sla/contracts/{contract.id}")
    if request.method == "POST":
        form = HealthCheckCompleteForm(request.POST)
        if form.is_valid():
            my_employee = _get_my_employee(request.user)
            check.status = "completed"
            check.findings = form.cleaned_data["findings"]
            check.performed_by = my_employee.id if my_employee else None
            check.completed_at = timezone.now()
            check.save()
            if check.project_id:
                log_audit(
                    check.project_id, request.user.id, action="health_check_completed",
                    new_value=check.scheduled_date.isoformat(),
                    note=f"Preventive visit completed for {contract.contract_number}.",
                )
            messages.success(request, "Health check visit completed and findings recorded.")
            return redirect(f"/projects/sla/contracts/{contract.id}")
        messages.error(request, "Record findings before completing the visit.")
    else:
        form = HealthCheckCompleteForm()
    return render(
        request,
        "project_management/health_check_complete.html",
        {
            "page_title": f"Complete Health Check #{check.id}",
            "check": check,
            "contract": contract,
            "form": form,
        },
    )


# ---------------------------------------------------------------------------
# Analytics and saved reports
# ---------------------------------------------------------------------------
@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def analytics(request):
    _mark_ticket_breaches()
    projects = list(visible_projects(request.user))
    task_counts = defaultdict(int)
    for row in ProjectTask.objects.values("status").annotate(total=Count("id")):
        task_counts[row["status"]] = row["total"]
    approved_expenses = ProjectExpense.objects.filter(status="approved").aggregate(
        total=Sum("amount")
    )["total"] or ZERO
    open_tickets = SlaTicket.objects.exclude(status__in=["resolved", "closed"]).count()
    overview = {
        "projects": len(projects),
        "in_execution": sum(1 for project in projects if project.status == "in_execution"),
        "contract_value": money(sum((p.contract_value or ZERO for p in projects), ZERO)),
        "expenses": money(approved_expenses),
        "open_tickets": open_tickets,
        "tasks_total": sum(task_counts.values()),
        "tasks_complete": task_counts.get("complete", 0),
        "tasks_blocked": task_counts.get("blocked", 0),
    }
    status_chart = run_query({"dataset": "projects", "dimension": "status", "metric": "count"})
    task_chart = run_query({"dataset": "tasks", "dimension": "status", "metric": "count"})
    expense_chart = run_query(
        {"dataset": "expenses", "dimension": "category", "metric": "sum_amount", "status": "approved"}
    )
    saved_reports = list(PmReportDefinition.objects.all()[:6])
    return render(
        request,
        "project_management/analytics.html",
        {
            "page_title": "Analytics - SORA ERP",
            "overview": overview,
            "status_chart": status_chart,
            "task_chart": task_chart,
            "expense_chart": expense_chart,
            "saved_reports": saved_reports,
            "datasets": DATASETS,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def analytics_query(request):
    filters = {
        "dataset": request.GET.get("dataset") or "projects",
        "dimension": request.GET.get("dimension") or "",
        "metric": request.GET.get("metric") or "",
        "status": request.GET.get("status") or "",
        "date_from": request.GET.get("date_from") or "",
        "date_to": request.GET.get("date_to") or "",
    }
    result = run_query(filters)
    filters = {
        "dataset": result["dataset"],
        "dimension": result["dimension"],
        "metric": result["metric"],
        "status": result["status"],
        "date_from": result["date_from"],
        "date_to": result["date_to"],
    }
    return render(
        request,
        "project_management/analytics_query.html",
        {
            "page_title": "Query Builder - SORA ERP",
            "result": result,
            "filters": filters,
            "datasets": DATASETS,
            "status_choices": STATUS_CHOICES,
            "save_form": ReportSaveForm(
                initial={
                    "name": f"{result['dataset_label']} by {result['dimension_label']}",
                    "description": "Saved from the analytics query builder.",
                }
            ),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def analytics_export(request):
    result = run_query(
        {
            "dataset": request.GET.get("dataset") or "projects",
            "dimension": request.GET.get("dimension") or "",
            "metric": request.GET.get("metric") or "",
            "status": request.GET.get("status") or "",
            "date_from": request.GET.get("date_from") or "",
            "date_to": request.GET.get("date_to") or "",
        }
    )
    return csv_response(
        f"pm-query-{result['dataset']}.csv",
        [result["dimension_label"], result["metric_label"], "% of max"],
        query_csv_rows(result),
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def reports(request):
    reports_rows = list(PmReportDefinition.objects.all().order_by("name"))
    creators = _user_names({row.created_by for row in reports_rows})
    for row in reports_rows:
        row.creator_name = creators.get(row.created_by, "—")
        row.recipient_count = len(
            [value for value in (row.recipients or "").replace(";", ",").split(",") if value.strip()]
        )
    return render(
        request,
        "project_management/reports.html",
        {
            "page_title": "Saved Reports - SORA ERP",
            "reports": reports_rows,
            "can_create": _access(request).can("projects", "create"),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
@require_POST
def report_save(request):
    if not _access(request).can("projects", "create"):
        messages.error(request, "You do not have permission to save reports.")
        return redirect("/projects/analytics/reports")
    form = ReportSaveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Provide a report name and delivery options.")
        return redirect("/projects/analytics/query")
    filters = {
        "dataset": request.POST.get("dataset") or "projects",
        "dimension": request.POST.get("dimension") or "status",
        "metric": request.POST.get("metric") or "count",
        "status": request.POST.get("status") or "",
        "date_from": request.POST.get("date_from") or "",
        "date_to": request.POST.get("date_to") or "",
    }
    report = PmReportDefinition.objects.create(
        name=form.cleaned_data["name"],
        description=form.cleaned_data.get("description") or "",
        report_type="query",
        filters=filters,
        schedule=form.cleaned_data["schedule"],
        recipients=form.cleaned_data.get("recipients") or "",
        created_by=request.user.id,
        created_at=timezone.now(),
    )
    messages.success(request, f"Report “{report.name}” saved.")
    return redirect(f"/projects/analytics/reports/{report.id}")


def _report_result(report):
    return run_query(report.filters or {})


def _report_recipients(report):
    raw = (report.recipients or "").replace(";", ",")
    return [value.strip() for value in raw.split(",") if value.strip()]


def _report_csv(report, result):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([result["dimension_label"], result["metric_label"], "% of max"])
    writer.writerows(query_csv_rows(result))
    return buffer.getvalue()


def _report_filters_text(result):
    parts = [f"Dataset: {result['dataset_label']}", f"Dimension: {result['dimension_label']}"]
    parts.append(f"Metric: {result['metric_label']}")
    if result["status"]:
        parts.append(f"Status: {result['status'].replace('_', ' ').title()}")
    if result["date_from"] or result["date_to"]:
        parts.append(f"Dates: {result['date_from'] or 'any'} to {result['date_to'] or 'any'}")
    return " · ".join(parts)


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def report_detail(request, report_id):
    report = get_object_or_404(PmReportDefinition, pk=report_id)
    result = _report_result(report)
    return render(
        request,
        "project_management/report_detail.html",
        {
            "page_title": f"{report.name} - SORA ERP",
            "report": report,
            "result": result,
            "filters_text": _report_filters_text(result),
            "recipients": _report_recipients(report),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def report_export(request, report_id):
    report = get_object_or_404(PmReportDefinition, pk=report_id)
    result = _report_result(report)
    return csv_response(
        f"pm-report-{report.id}.csv",
        [result["dimension_label"], result["metric_label"], "% of max"],
        query_csv_rows(result),
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def report_pdf_download(request, report_id):
    report = get_object_or_404(PmReportDefinition, pk=report_id)
    result = _report_result(report)
    rows = [
        [row["label"], f"{row['value']:,.2f}", f"{row['percent']}%"]
        for row in result["rows"]
    ]
    content = report_pdf(
        report.name,
        f"SORA ERP · {result['dataset_label']}",
        [result["dimension_label"], result["metric_label"], "% of max"],
        rows,
        _report_filters_text(result),
    )
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="pm-report-{report.id}.pdf"'
    return response


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
@require_POST
def report_dispatch(request, report_id):
    report = get_object_or_404(PmReportDefinition, pk=report_id)
    recipients = _report_recipients(report)
    if not recipients:
        messages.error(request, "Add at least one recipient before dispatching this report.")
        return redirect(f"/projects/analytics/reports/{report.id}")
    result = _report_result(report)
    csv_content = _report_csv(report, result)
    pdf_content = report_pdf(
        report.name,
        f"SORA ERP · {result['dataset_label']}",
        [result["dimension_label"], result["metric_label"], "% of max"],
        [
            [row["label"], f"{row['value']:,.2f}", f"{row['percent']}%"]
            for row in result["rows"]
        ],
        _report_filters_text(result),
    )
    failures = []
    sent = 0
    for recipient in recipients:
        ok, error = send_pm_email(
            subject=f"SORA PM report · {report.name}",
            body=(
                f"Hello,\n\nPlease find attached the scheduled PM report “{report.name}”.\n\n"
                f"{_report_filters_text(result)}\n\nRegards,\nSORA ERP"
            ),
            recipient=recipient,
            attachments=[
                (f"{report.name}.csv", csv_content, "text/csv"),
                (f"{report.name}.pdf", pdf_content, "application/pdf"),
            ],
        )
        if ok:
            sent += 1
        else:
            failures.append(f"{recipient}: {error}")
    if sent:
        report.last_run_at = timezone.now()
        report.save(update_fields=["last_run_at"])
        messages.success(request, f"Report sent to {sent} recipient(s).")
    for failure in failures:
        messages.error(request, f"Delivery failed — {failure}")
    return redirect(f"/projects/analytics/reports/{report.id}")


# ---------------------------------------------------------------------------
# Custom dashboards
# ---------------------------------------------------------------------------
def _widget_payloads(user, keys):
    projects = list(visible_projects(user))
    tasks = list(ProjectTask.objects.filter(project_id__in=[p.id for p in projects]))
    project_names = {row.id: row.project_code for row in projects}
    payloads = {}

    financials = [project_financials(project) for project in projects]
    total_contract = sum((row["contract_value"] for row in financials), ZERO)
    total_cost = sum((row["total_cost"] for row in financials), ZERO)
    total_margin = total_contract - total_cost
    margin_percent = round(float(total_margin / total_contract * 100), 1) if total_contract else 0
    alerts = expense_burn_alerts(limit=6)

    if "portfolio_summary" in keys:
        payloads["portfolio_summary"] = {
            "label": "Portfolio summary",
            "projects": len(projects),
            "active": sum(1 for p in projects if p.status == "in_execution"),
            "contract": money(total_contract),
            "cost": money(total_cost),
        }
    if "financial_health" in keys:
        payloads["financial_health"] = {
            "label": "Financial health",
            "contract": money(total_contract),
            "total_cost": money(total_cost),
            "margin": money(total_margin),
            "margin_percent": margin_percent,
        }
    if "expense_burn" in keys:
        payloads["expense_burn"] = {"label": "Expense burn alerts", "alerts": alerts}
    if "project_status" in keys:
        counts = defaultdict(int)
        for project in projects:
            counts[project.status] += 1
        labels = dict(PROJECT_STATUSES)
        payloads["project_status"] = {
            "label": "Projects by status",
            "rows": [
                {"label": labels.get(key, key), "value": value}
                for key, value in sorted(counts.items(), key=lambda item: item[1], reverse=True)
            ],
        }
    if "task_status" in keys:
        counts = defaultdict(int)
        for task in tasks:
            counts[task.status] += 1
        labels = dict(TASK_STATUSES)
        payloads["task_status"] = {
            "label": "Tasks by status",
            "rows": [
                {"label": labels.get(key, key), "value": value}
                for key, value in sorted(counts.items(), key=lambda item: item[1], reverse=True)
            ],
        }
    if "upcoming_deadlines" in keys:
        today = timezone.localdate()
        upcoming = sorted(
            [
                task
                for task in tasks
                if task.deadline_date and task.status != "complete"
                and task.deadline_date <= today + timedelta(days=14)
            ],
            key=lambda task: task.deadline_date,
        )[:8]
        employees = _employees({task.assigned_to for task in upcoming if task.assigned_to})
        rows = []
        for task in upcoming:
            rows.append(
                {
                    "task": task,
                    "project_code": project_names.get(task.project_id, ""),
                    "assignee": employee_label(employees.get(task.assigned_to)),
                    "overdue": task.deadline_date < today,
                }
            )
        payloads["upcoming_deadlines"] = {"label": "Upcoming deadlines", "rows": rows}
    if "sla_snapshot" in keys:
        open_tickets = SlaTicket.objects.exclude(status__in=["resolved", "closed"])
        payloads["sla_snapshot"] = {
            "label": "SLA snapshot",
            "open": open_tickets.count(),
            "breached": open_tickets.filter(breached=True).count(),
            "critical": open_tickets.filter(priority="critical").count(),
            "contracts": SlaContract.objects.filter(status="active").count(),
        }
    if "team_workload" in keys:
        workload = {}
        for task in tasks:
            if not task.assigned_to or task.status == "complete":
                continue
            workload[task.assigned_to] = workload.get(task.assigned_to, 0) + 1
        employees = _employees(set(workload))
        rows = [
            {
                "name": employee_label(employees.get(employee_id)),
                "open_tasks": count,
            }
            for employee_id, count in sorted(workload.items(), key=lambda item: item[1], reverse=True)[:8]
        ]
        payloads["team_workload"] = {"label": "Team workload", "rows": rows}
    if "recent_activity" in keys:
        payloads["recent_activity"] = {
            "label": "Recent activity",
            "rows": _audit_rows(ProjectAuditLog.objects.order_by("-created_at", "-id")[:8]),
        }
    widgets = []
    for key in keys:
        if key in payloads:
            payloads[key]["key"] = key
            widgets.append(payloads[key])
    return widgets


def _save_dashboard(request, dashboard=None):
    form = DashboardForm(request.POST)
    if not form.is_valid():
        return None, form
    my_dashboards = PmDashboard.objects.filter(user_id=request.user.id)
    if form.cleaned_data.get("is_default"):
        my_dashboards.exclude(pk=getattr(dashboard, "pk", None)).update(is_default=False)
    if dashboard is None:
        dashboard = PmDashboard(user_id=request.user.id, created_at=timezone.now())
    dashboard.title = form.cleaned_data["title"]
    dashboard.is_default = bool(form.cleaned_data.get("is_default"))
    dashboard.layout_config = {"widgets": form.cleaned_data["widget_order"]}
    dashboard.save()
    return dashboard, form


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def dashboards(request):
    dashboards_rows = list(PmDashboard.objects.filter(user_id=request.user.id).order_by("-is_default", "title"))
    return render(
        request,
        "project_management/dashboards.html",
        {
            "page_title": "My Dashboards - SORA ERP",
            "dashboards": dashboards_rows,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def dashboard_create(request):
    if request.method == "POST":
        dashboard, form = _save_dashboard(request)
        if dashboard:
            messages.success(request, "Dashboard created.")
            return redirect(f"/projects/dashboards/{dashboard.id}")
        messages.error(request, "Choose at least one widget and give the dashboard a title.")
    else:
        form = DashboardForm(
            initial={
                "widgets": [key for key, _ in WIDGET_CHOICES[:5]],
                "widget_order": ",".join(key for key, _ in WIDGET_CHOICES[:5]),
            }
        )
    return render(
        request,
        "project_management/dashboard_form.html",
        {
            "page_title": "New Dashboard - SORA ERP",
            "form": form,
            "is_edit": False,
            "widget_choices": WIDGET_CHOICES,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def dashboard_edit(request, dashboard_id):
    dashboard = get_object_or_404(PmDashboard, pk=dashboard_id, user_id=request.user.id)
    if request.method == "POST":
        saved, form = _save_dashboard(request, dashboard)
        if saved:
            messages.success(request, "Dashboard updated.")
            return redirect(f"/projects/dashboards/{saved.id}")
        messages.error(request, "Choose at least one widget and give the dashboard a title.")
    else:
        widgets = (dashboard.layout_config or {}).get("widgets") or []
        form = DashboardForm(
            initial={
                "title": dashboard.title,
                "is_default": dashboard.is_default,
                "widgets": widgets,
                "widget_order": ",".join(widgets),
            }
        )
    return render(
        request,
        "project_management/dashboard_form.html",
        {
            "page_title": f"Edit {dashboard.title} - SORA ERP",
            "form": form,
            "dashboard": dashboard,
            "is_edit": True,
            "widget_choices": WIDGET_CHOICES,
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
def dashboard_detail(request, dashboard_id):
    dashboard = get_object_or_404(PmDashboard, pk=dashboard_id, user_id=request.user.id)
    widget_keys = (dashboard.layout_config or {}).get("widgets") or []
    return render(
        request,
        "project_management/dashboard_detail.html",
        {
            "page_title": f"{dashboard.title} - SORA ERP",
            "dashboard": dashboard,
            "widgets": _widget_payloads(request.user, widget_keys),
        },
    )


@login_required
@module_access_required("projects")
@pm_flag_required("MODULE_ANALYTICS")
@require_POST
def dashboard_delete(request, dashboard_id):
    dashboard = get_object_or_404(PmDashboard, pk=dashboard_id, user_id=request.user.id)
    title = dashboard.title
    dashboard.delete()
    messages.success(request, f"Dashboard “{title}” deleted.")
    return redirect("/projects/dashboards")


# ---------------------------------------------------------------------------
# Licence & feature flags
# ---------------------------------------------------------------------------
@login_required
@admin_required
def license_features(request):
    ensure_feature_flags()
    flags = list(PmFeatureFlag.objects.all().order_by("tier", "flag_key"))
    grouped = {"core": [], "standard": [], "enterprise": []}
    for flag in flags:
        grouped.setdefault(flag.tier, []).append(flag)
    return render(
        request,
        "project_management/license.html",
        {
            "page_title": "PM Licence & Features - SORA ERP",
            "flags": flags,
            "grouped": grouped,
            "enabled_count": sum(1 for flag in flags if flag.enabled),
        },
    )


@login_required
@admin_required
@require_POST
def flag_toggle(request, flag_id):
    ensure_feature_flags()
    flag = get_object_or_404(PmFeatureFlag, pk=flag_id)
    flag.enabled = not flag.enabled
    flag.save(update_fields=["enabled", "updated_at"])
    messages.success(
        request,
        f"{flag.label} is now {'enabled' if flag.enabled else 'disabled'} "
        f"for the {flag.get_tier_display()} tier.",
    )
    return redirect("/projects/license")
