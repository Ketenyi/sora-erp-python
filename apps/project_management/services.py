"""Shared PM services: financials, audit trail, magic links, scoring, queries."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from apps.core.mail import MailNotConfigured, module_mail_config, send_module_email
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.core.models import (
    Employee,
    PmFeatureFlag,
    Project,
    ProjectAuditLog,
    ProjectChangeRequest,
    ProjectExpense,
    ProjectInvoice,
    ProjectMagicLink,
    ProjectTask,
    ProjectTeam,
    ProjectTimeEntry,
    SlaContract,
    SlaHealthCheck,
    SlaTicket,
)

ZERO = Decimal("0.00")
EXPENSE_ALERT_THRESHOLD = Decimal("0.80")
MAGIC_LINK_TTL_HOURS = 72

DEFAULT_FEATURE_FLAGS = [
    ("FEATURE_KANBAN", "Kanban board", "core"),
    ("FEATURE_TASKS", "Task management", "core"),
    ("FEATURE_USERS", "Team members & scorecards", "core"),
    ("MODULE_TENDER", "Tender & Gantt", "standard"),
    ("MODULE_MOM_SHIELD", "MOM Shield & change requests", "standard"),
    ("MODULE_FIELD_EXPENSES", "Field expenses & P&L", "standard"),
    ("MODULE_SLA_HUB", "SLA & support hub", "enterprise"),
    ("MODULE_HEALTH_CHECKS", "Preventive health checks", "enterprise"),
    ("MODULE_ANALYTICS", "Analytics, dashboards & reports", "enterprise"),
]


def ensure_feature_flags():
    for key, label, tier in DEFAULT_FEATURE_FLAGS:
        PmFeatureFlag.objects.get_or_create(
            flag_key=key, defaults={"label": label, "tier": tier, "enabled": True}
        )


def flag_enabled(key: str) -> bool:
    row = PmFeatureFlag.objects.filter(flag_key=key).first()
    return row.enabled if row else True


def money(value) -> Decimal:
    return (value or ZERO).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
def log_audit(project_id, user_id=None, action="update", task_id=None, field_name=None,
              old_value=None, new_value=None, note=None):
    return ProjectAuditLog.objects.create(
        project_id=project_id,
        task_id=task_id,
        user_id=user_id,
        action=action,
        field_name=field_name,
        old_value="" if old_value is None else str(old_value),
        new_value="" if new_value is None else str(new_value),
        note=note or "",
        created_at=timezone.now(),
    )


def task_history(task_id, limit=50):
    return ProjectAuditLog.objects.filter(task_id=task_id)[:limit]


# ---------------------------------------------------------------------------
# Financials and health
# ---------------------------------------------------------------------------
def project_financials(project: Project) -> dict:
    expenses = list(ProjectExpense.objects.filter(project_id=project.id))
    approved_expenses = sum(
        (row.amount or ZERO for row in expenses if row.status == "approved"), ZERO
    )
    pending_expenses = sum(
        (row.amount or ZERO for row in expenses if row.status == "pending"), ZERO
    )

    labour = ZERO
    for entry in ProjectTimeEntry.objects.filter(project_id=project.id, status="approved"):
        rate = entry.hourly_rate or ZERO
        labour += entry.total_amount or (entry.hours or ZERO) * rate

    invoiced = sum(
        (row.allocated_amount or ZERO for row in ProjectInvoice.objects.filter(project_id=project.id)),
        ZERO,
    )
    billed = max(invoiced, project.actual_revenue or ZERO)
    contract = project.contract_value or project.budget_amount or ZERO
    expense_budget = project.allocated_expense_budget or ZERO
    total_cost = approved_expenses + labour
    margin = contract - total_cost
    realized_margin = billed - total_cost
    burn_percent = (
        float(approved_expenses / expense_budget * 100) if expense_budget > 0 else 0.0
    )
    margin_percent = float(margin / contract * 100) if contract > 0 else 0.0
    realized_margin_percent = (
        float(realized_margin / billed * 100) if billed > 0 else 0.0
    )

    categories = []
    totals = {}
    for row in expenses:
        if row.status != "approved":
            continue
        key = row.category or "Other"
        totals[key] = totals.get(key, ZERO) + (row.amount or ZERO)
    for label, amount in sorted(totals.items(), key=lambda item: item[1], reverse=True):
        categories.append(
            {
                "label": label.replace("_", " ").title(),
                "amount": money(amount),
                "percent": round(float(amount / approved_expenses * 100), 1)
                if approved_expenses
                else 0,
            }
        )

    return {
        "contract_value": money(contract),
        "allocated_expense_budget": money(expense_budget),
        "approved_expenses": money(approved_expenses),
        "pending_expenses": money(pending_expenses),
        "labour_cost": money(labour),
        "total_cost": money(total_cost),
        "billed": money(billed),
        "margin": money(margin),
        "realized_margin": money(realized_margin),
        "margin_percent": round(margin_percent, 1),
        "realized_margin_percent": round(realized_margin_percent, 1),
        "expense_burn_percent": round(burn_percent, 1),
        "expense_alert": expense_budget > 0 and approved_expenses >= expense_budget * EXPENSE_ALERT_THRESHOLD,
        "expense_categories": categories,
    }


def task_summary(project_id: int) -> dict:
    tasks = list(ProjectTask.objects.filter(project_id=project_id))
    total = len(tasks)
    completed = sum(1 for task in tasks if task.status == "complete")
    counts = {key: 0 for key, _ in ProjectTask._meta.get_field("status").choices}
    for task in tasks:
        if task.status in counts:
            counts[task.status] += 1
    today = timezone.localdate()
    overdue = sum(
        1
        for task in tasks
        if task.deadline_date
        and task.deadline_date < today
        and task.status != "complete"
    )
    completion = round(completed / total * 100, 1) if total else 0.0
    return {
        "total": total,
        "completed": completed,
        "open": total - completed,
        "overdue": overdue,
        "blocked": counts.get("blocked", 0),
        "completion_percent": completion,
        "counts": counts,
    }


def project_health(project: Project, financials=None, summary=None) -> dict:
    financials = financials or project_financials(project)
    summary = summary or task_summary(project.id)
    label, css = "On Track", "success"
    reasons = []
    if financials["expense_alert"]:
        label, css = "At Risk", "warning"
        reasons.append(
            f"Expense burn at {financials['expense_burn_percent']:.0f}% of allocated budget"
        )
    if summary["blocked"]:
        label = "At Risk" if css == "success" else label
        css = "warning" if css == "success" else css
        reasons.append(f"{summary['blocked']} blocked task(s)")
    if summary["overdue"]:
        label, css = "At Risk" if css == "success" else label, "warning" if css == "success" else css
        reasons.append(f"{summary['overdue']} overdue task(s)")
    if financials["margin"] < 0:
        label, css = "Critical", "danger"
        reasons.append("Projected margin is negative")
    return {"label": label, "css": css, "reasons": reasons}


def expense_burn_alerts(limit=5):
    rows = []
    projects = Project.objects.exclude(status__in=["completed"]).order_by("-id")
    for project in projects:
        financials = project_financials(project)
        if financials["expense_alert"]:
            rows.append({"project": project, "financials": financials})
        if len(rows) >= limit:
            break
    return rows


# ---------------------------------------------------------------------------
# Magic links
# ---------------------------------------------------------------------------
def _hash_token(raw_token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), raw_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def issue_magic_link(project, action_type, target_entity_id, recipient_email="", user_id=None,
                     ttl_hours=MAGIC_LINK_TTL_HOURS):
    now = timezone.now()
    ProjectMagicLink.objects.filter(
        project_id=project.id,
        action_type=action_type,
        target_entity_id=target_entity_id,
        used_at__isnull=True,
    ).update(used_at=now)
    raw_token = secrets.token_hex(32)
    link = ProjectMagicLink.objects.create(
        project_id=project.id,
        action_type=action_type,
        target_entity_id=int(target_entity_id),
        token_hash=_hash_token(raw_token),
        expires_at=now + timedelta(hours=ttl_hours),
        recipient_email=recipient_email or project.client_email or "",
        created_by=user_id,
        created_at=now,
    )
    return link, raw_token


def resolve_magic_link(raw_token: str):
    if not raw_token:
        return None
    return (
        ProjectMagicLink.objects.all_tenants()
        .filter(token_hash=_hash_token(raw_token))
        .first()
    )


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def mail_config() -> dict | None:
    return module_mail_config("projects")


def send_pm_email(subject, body, recipient, attachments=None) -> tuple[bool, str]:
    if not recipient:
        return False, "No recipient email address was supplied."
    try:
        delivered = send_module_email(
            "projects",
            subject,
            body,
            [recipient],
            attachments=attachments,
            fail_silently=False,
        )
    except MailNotConfigured as exc:
        return False, f"{exc} Configure a Project sender under Settings > Sender Identities."
    except Exception as exc:  # pragma: no cover - external SMTP
        return False, str(exc)
    if not delivered:
        return False, "The mail server did not accept the message."
    return True, ""


# ---------------------------------------------------------------------------
# Scorecards
# ---------------------------------------------------------------------------
def field_employee_map(ids) -> dict[int, Employee]:
    ids = {pk for pk in ids if pk}
    if not ids:
        return {}
    return {row.id: row for row in Employee.objects.filter(id__in=ids)}


def employee_label(employee: Employee | None) -> str:
    if not employee:
        return "Unassigned"
    return f"{employee.first_name} {employee.last_name}".strip() or employee.employee_code


def employee_scorecards() -> list[dict]:
    employees = {row.id: row for row in Employee.objects.filter(status="active")}
    tasks = list(ProjectTask.objects.all())
    logs = list(
        ProjectAuditLog.objects.filter(action="status_change", task_id__isnull=False).order_by(
            "task_id", "created_at"
        )
    )
    logs_by_task: dict[int, list] = {}
    for log in logs:
        logs_by_task.setdefault(log.task_id, []).append(log)

    today = timezone.localdate()

    def blocker_hours(task_id):
        rows = logs_by_task.get(task_id, [])
        total = 0.0
        blocked_at = None
        for row in rows:
            if row.new_value == "blocked" and blocked_at is None:
                blocked_at = row.created_at
            elif blocked_at is not None and row.new_value != "blocked":
                total += (row.created_at - blocked_at).total_seconds() / 3600
                blocked_at = None
        return total

    buckets: dict[int, dict] = {}
    for task in tasks:
        if not task.assigned_to:
            continue
        bucket = buckets.setdefault(
            task.assigned_to,
            {
                "tasks": [],
                "logs": 0,
            },
        )
        bucket["tasks"].append(task)
        bucket["logs"] += len(logs_by_task.get(task.id, []))

    rows = []
    for employee_id, bucket in buckets.items():
        employee = employees.get(employee_id)
        if not employee:
            continue
        member_tasks = bucket["tasks"]
        total = len(member_tasks)
        completed_tasks = [task for task in member_tasks if task.status == "complete"]
        deadline_tasks = [task for task in completed_tasks if task.deadline_date and task.completion_timestamp]
        on_time = sum(
            1
            for task in deadline_tasks
            if timezone.localtime(task.completion_timestamp).date() <= task.deadline_date
        )
        overdue = sum(
            1
            for task in member_tasks
            if task.deadline_date and task.deadline_date < today and task.status != "complete"
        )
        blocked = sum(1 for task in member_tasks if task.status == "blocked")
        completion_rate = len(completed_tasks) / total * 100 if total else 0
        punctuality_rate = on_time / len(deadline_tasks) * 100 if deadline_tasks else completion_rate
        hours = [blocker_hours(task.id) for task in member_tasks]
        average_blocker = sum(hours) / len(hours) if hours else 0
        delivery_score = completion_rate * 0.4
        punctuality_score = punctuality_rate * 0.3
        blocker_score = max(0.0, 20.0 - min(20.0, average_blocker / 6))
        audit_score = 10.0 if bucket["logs"] else 0.0
        score = round(delivery_score + punctuality_score + blocker_score + audit_score, 1)
        rows.append(
            {
                "employee": employee,
                "name": employee_label(employee),
                "job_title": employee.job_title or "Team member",
                "total": total,
                "completed": len(completed_tasks),
                "completion_rate": round(completion_rate, 1),
                "on_time_rate": round(punctuality_rate, 1),
                "overdue": overdue,
                "blocked": blocked,
                "average_blocker_hours": round(average_blocker, 1),
                "audit_actions": bucket["logs"],
                "delivery_score": round(delivery_score, 1),
                "punctuality_score": round(punctuality_score, 1),
                "blocker_score": round(blocker_score, 1),
                "audit_score": round(audit_score, 1),
                "score": score,
            }
        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


# ---------------------------------------------------------------------------
# Analytics query builder
# ---------------------------------------------------------------------------
DATASETS = {
    "projects": {
        "label": "Projects",
        "dimensions": {
            "status": "Status",
            "priority": "Priority",
            "project_type": "Project type",
            "manager": "Manager",
            "month": "Created month",
        },
        "metrics": {
            "count": "Project count",
            "sum_contract": "Contract value",
            "avg_contract": "Average contract value",
        },
    },
    "tasks": {
        "label": "Tasks",
        "dimensions": {
            "status": "Status",
            "priority": "Priority",
            "project": "Project",
            "assignee": "Assignee",
            "month": "Created month",
        },
        "metrics": {
            "count": "Task count",
            "sum_estimated": "Estimated hours",
            "sum_actual": "Actual hours",
        },
    },
    "expenses": {
        "label": "Field expenses",
        "dimensions": {
            "status": "Status",
            "category": "Category",
            "project": "Project",
            "month": "Expense month",
        },
        "metrics": {
            "count": "Expense count",
            "sum_amount": "Expense amount",
            "avg_amount": "Average expense",
        },
    },
    "tickets": {
        "label": "SLA tickets",
        "dimensions": {
            "status": "Status",
            "priority": "Priority",
            "contract": "Contract",
            "assignee": "Assignee",
            "month": "Created month",
        },
        "metrics": {"count": "Ticket count"},
    },
}

STATUS_CHOICES = {
    "projects": [value for value, _ in Project._meta.get_field("status").choices],
    "tasks": [value for value, _ in ProjectTask._meta.get_field("status").choices],
    "expenses": ["pending", "approved", "rejected"],
    "tickets": [value for value, _ in SlaTicket._meta.get_field("status").choices],
}

DIMENSION_FIELDS = {
    "manager": "manager_id",
    "project": "project_id",
    "assignee": "assigned_to",
    "contract": "contract_id",
}


def _base_queryset(dataset):
    if dataset == "projects":
        return Project.objects.all()
    if dataset == "tasks":
        return ProjectTask.objects.all()
    if dataset == "expenses":
        return ProjectExpense.objects.all()
    return SlaTicket.objects.all()


def _metric_expression(dataset, metric):
    if metric == "count":
        return Count("id")
    if dataset == "projects":
        return {"sum_contract": Sum("contract_value"), "avg_contract": Avg("contract_value")}[metric]
    if dataset == "tasks":
        return {"sum_estimated": Sum("estimated_hours"), "sum_actual": Sum("actual_hours")}[metric]
    if dataset == "expenses":
        return {"sum_amount": Sum("amount"), "avg_amount": Avg("amount")}[metric]
    return Count("id")


def _month_field(dataset):
    if dataset in {"projects", "tasks", "tickets"}:
        return "created_at"
    return "expense_date"


def _label_lookup(dataset, dimension, keys):
    keys = [key for key in keys if key is not None]
    if dimension in {"project", "contract"}:
        if dimension == "project":
            return {
                row["id"]: f"{row['project_code']} · {row['project_name']}"
                for row in Project.objects.filter(id__in=keys).values(
                    "id", "project_code", "project_name"
                )
            }
        return {
            row["id"]: row["contract_number"]
            for row in SlaContract.objects.filter(id__in=keys).values("id", "contract_number")
        }
    if dimension in {"assignee", "manager"}:
        return {
            employee_id: employee_label(employee)
            for employee_id, employee in field_employee_map(keys).items()
        }
    if dimension == "priority":
        return {key: key.title() for key in keys}
    if dimension == "category":
        return {key: key.replace("_", " ").title() for key in keys}
    return {key: key.replace("_", " ").title() for key in keys}


def run_query(filters: dict) -> dict:
    dataset = filters.get("dataset") or "projects"
    dimension = filters.get("dimension") or "status"
    metric = filters.get("metric") or "count"
    status = filters.get("status") or ""
    date_from = filters.get("date_from") or ""
    date_to = filters.get("date_to") or ""

    if dataset not in DATASETS:
        dataset = "projects"
    config = DATASETS[dataset]
    if dimension not in config["dimensions"]:
        dimension = "status"
    if metric not in config["metrics"]:
        metric = "count"
    status = status if status in STATUS_CHOICES.get(dataset, []) else ""

    qs = _base_queryset(dataset)
    if status:
        qs = qs.filter(status=status)
    date_field = _month_field(dataset)
    if date_from:
        qs = qs.filter(**{f"{date_field}__date__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{date_field}__date__lte": date_to})

    rows = []
    if dimension == "month":
        series = (
            qs.annotate(month=TruncMonth(date_field))
            .values("month")
            .annotate(value=_metric_expression(dataset, metric))
            .order_by("month")
        )
        for row in series:
            label = row["month"].strftime("%b %Y") if row["month"] else "Unknown"
            rows.append({"label": label, "value": row["value"] or 0})
    else:
        dimension_field = DIMENSION_FIELDS.get(dimension, dimension)
        series = (
            qs.values(dimension_field)
            .annotate(value=_metric_expression(dataset, metric))
            .order_by("-value")
        )
        keys = [row[dimension_field] for row in series]
        labels = _label_lookup(dataset, dimension, keys)
        for row in series:
            key = row[dimension_field]
            if dimension in {"assignee", "manager", "project", "contract"}:
                label = labels.get(key, "Unassigned")
            elif key in (None, ""):
                label = "Not set"
            else:
                label = labels.get(key, str(key))
            rows.append({"label": label, "value": row["value"] or 0})

    total = sum((Decimal(str(row["value"])) for row in rows), ZERO)
    maximum = max((Decimal(str(row["value"])) for row in rows), default=ZERO)
    for row in rows:
        row["value"] = float(row["value"] or 0)
        row["percent"] = round(float(Decimal(str(row["value"])) / maximum * 100), 1) if maximum else 0

    columns = 2
    return {
        "dataset": dataset,
        "dimension": dimension,
        "metric": metric,
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
        "rows": rows,
        "total": total,
        "columns": columns,
        "dataset_label": config["label"],
        "dimension_label": config["dimensions"][dimension],
        "metric_label": config["metrics"][metric],
        "has_values": bool(rows),
    }


def query_csv_rows(result):
    return [[row["label"], row["value"], f"{row['percent']}%"] for row in result["rows"]]
