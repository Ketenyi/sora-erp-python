"""Helpdesk workspace: queues, ticket conversations, SLA policies and visits."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.core.mail import mail_is_configured
from apps.core.models import (
    Employee,
    Project,
    SlaContract,
    SlaHealthCheck,
    SlaTicket,
)
from apps.core.permissions import get_access, module_access_required
from apps.core.services import get_company_settings, next_code, next_sequence
from apps.core.tenancy import tenant_context
from apps.core.ui import paginate
from apps.helpdesk import services
from apps.helpdesk.forms import (
    POLICY_ORDER,
    ContractForm,
    HealthCheckCompleteForm,
    HealthCheckForm,
    HelpdeskMailboxForm,
    HelpdeskSurveyForm,
    ReplyForm,
    RoutingRuleForm,
    SlaPolicyForm,
    SurveyResponseForm,
    TicketForm,
    TicketUpdateForm,
)
from apps.helpdesk.models import (
    PRIORITY_CHOICES,
    HelpdeskAgent,
    HelpdeskEvent,
    HelpdeskInboundMessage,
    HelpdeskRoutingRule,
    HelpdeskSurveyResponse,
    TicketReply,
)
from apps.project_management.services import employee_label, field_employee_map, log_audit

ZERO = Decimal("0.00")
PAGE_SIZE = 15
CLOSED_STATUSES = {"resolved", "closed"}


def _access(request):
    return get_access(request.user)


def _money(value) -> Decimal:
    return (value or ZERO).quantize(Decimal("0.01"))


def _can_manage(request) -> bool:
    return _access(request).can("helpdesk", "manage")


def _settings_guard(request):
    if _can_manage(request):
        return None
    messages.error(
        request, "Only helpdesk administrators can change automation settings."
    )
    return redirect("/helpdesk/dashboard")



def _employee_for_user(user):
    if not getattr(user, "email", ""):
        return None
    return Employee.objects.filter(email__iexact=user.email, status="active").first()


def _contract_map(ids):
    ids = {pk for pk in ids if pk}
    if not ids:
        return {}
    return {row.id: row for row in SlaContract.objects.filter(id__in=ids)}


def _project_codes(ids):
    ids = {pk for pk in ids if pk}
    if not ids:
        return {}
    return {row.id: row.project_code for row in Project.objects.filter(id__in=ids)}


def _decorate_tickets(rows):
    rows = list(rows)
    contracts = _contract_map({row.contract_id for row in rows})
    projects = _project_codes({row.project_id for row in rows if row.project_id})
    employees = field_employee_map({row.assigned_to for row in rows if row.assigned_to})
    now = timezone.now()
    for row in rows:
        contract = contracts.get(row.contract_id)
        row.contract_number = contract.contract_number if contract else f"Contract #{row.contract_id}"
        row.project_code = projects.get(row.project_id, "")
        row.assignee_name = employee_label(employees.get(row.assigned_to))
        row.is_overdue = bool(
            row.sla_due_at and row.sla_due_at < now and row.status not in CLOSED_STATUSES
        )
        row.age_hours = round((now - row.created_at).total_seconds() / 3600, 1) if row.created_at else 0
        if row.sla_due_at and row.status not in CLOSED_STATUSES:
            seconds = (row.sla_due_at - now).total_seconds()
            row.hours_left = round(seconds / 3600, 1)
        else:
            row.hours_left = None
    return rows


@login_required
@module_access_required("helpdesk")
def dashboard(request):
    services.mark_breaches()
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    open_tickets = SlaTicket.objects.exclude(status__in=CLOSED_STATUSES)
    resolved_month = SlaTicket.objects.filter(resolved_at__gte=month_start).exclude(
        status__in=["open", "in_progress", "waiting_client"]
    )
    responded = SlaTicket.objects.filter(
        first_response_at__isnull=False, created_at__gte=now - timedelta(days=30)
    )
    response_hours = [
        (ticket.first_response_at - ticket.created_at).total_seconds() / 3600
        for ticket in responded
        if ticket.created_at and ticket.first_response_at
    ]
    contracts = list(SlaContract.objects.all())
    active_contracts = [row for row in contracts if row.status == "active"]
    recent = _decorate_tickets(open_tickets.order_by("-created_at", "-id")[:8])
    upcoming_checks = list(
        SlaHealthCheck.objects.filter(status="scheduled").order_by("scheduled_date", "id")[:6]
    )
    contract_numbers = {row.id: row.contract_number for row in contracts}
    for check in upcoming_checks:
        check.contract_number = contract_numbers.get(
            check.contract_id, f"Contract #{check.contract_id}"
        )
    priority_counts = {
        row["priority"]: row["count"]
        for row in open_tickets.values("priority").annotate(count=Count("id"))
    }
    agent_rows = []
    workload = (
        open_tickets.values("assigned_to").annotate(total=Count("id")).order_by("-total")[:6]
    )
    employees = field_employee_map({row["assigned_to"] for row in workload if row["assigned_to"]})
    for row in workload:
        agent_rows.append(
            {
                "name": employee_label(employees.get(row["assigned_to"]))
                if row["assigned_to"]
                else "Unassigned",
                "count": row["total"],
            }
        )
    survey_stats = HelpdeskSurveyResponse.objects.filter(
        submitted_at__gte=now - timedelta(days=90)
    ).aggregate(avg=Avg("rating"), total=Count("id"))
    mailbox = services.helpdesk_setting()
    stats = {
        "open": open_tickets.count(),
        "unassigned": open_tickets.filter(assigned_to__isnull=True).count(),
        "breached": open_tickets.filter(breached=True).count(),
        "response_breached": open_tickets.filter(response_breached=True).count(),
        "critical": priority_counts.get("critical", 0),
        "resolved_month": resolved_month.count(),
        "awaiting_confirmation": SlaTicket.objects.filter(
            status="resolved", client_confirmed_at__isnull=True
        ).count(),
        "avg_response": round(sum(response_hours) / len(response_hours), 1) if response_hours else 0,
        "csat": round(survey_stats["avg"], 1) if survey_stats["avg"] else 0,
        "survey_count": survey_stats["total"] or 0,
        "contracts": len(active_contracts),
        "mrr": _money(sum((row.monthly_value or ZERO for row in active_contracts), ZERO)),
    }
    return render(
        request,
        "helpdesk/dashboard.html",
        {
            "page_title": "Helpdesk Dashboard - SORA ERP",
            "stats": stats,
            "priority_counts": priority_counts,
            "recent_tickets": recent,
            "upcoming_checks": upcoming_checks,
            "agent_rows": agent_rows,
            "mailbox": mailbox,
            "can_create": _access(request).can("helpdesk", "create"),
            "can_manage": _can_manage(request),
        },
    )


@login_required
@module_access_required("helpdesk")
def tickets(request):
    services.mark_breaches()
    queryset = SlaTicket.objects.all().order_by("-created_at", "-id")
    query = (request.GET.get("q") or "").strip()
    status_filter = request.GET.get("status") or ""
    priority_filter = request.GET.get("priority") or ""
    category_filter = request.GET.get("category") or ""
    contract_filter = request.GET.get("contract") or ""
    assignee_filter = request.GET.get("assignee") or ""
    if query:
        queryset = queryset.filter(
            Q(ticket_number__icontains=query)
            | Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(requester_name__icontains=query)
            | Q(requester_email__icontains=query)
        )
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if priority_filter:
        queryset = queryset.filter(priority=priority_filter)
    if category_filter:
        queryset = queryset.filter(category=category_filter)
    if contract_filter.isdigit():
        queryset = queryset.filter(contract_id=int(contract_filter))
    if assignee_filter == "me":
        employee = _employee_for_user(request.user)
        queryset = queryset.filter(assigned_to=employee.id) if employee else queryset.none()
    elif assignee_filter == "unassigned":
        queryset = queryset.filter(assigned_to__isnull=True)
    elif assignee_filter.isdigit():
        queryset = queryset.filter(assigned_to=int(assignee_filter))
    page_obj = paginate(request, queryset, PAGE_SIZE)
    rows = _decorate_tickets(page_obj.object_list)
    return render(
        request,
        "helpdesk/tickets.html",
        {
            "page_title": "Ticket Queue - SORA ERP",
            "page_obj": page_obj,
            "rows": rows,
            "tickets": page_obj.object_list,
            "contracts": SlaContract.objects.all().order_by("contract_number"),
            "agents": Employee.objects.filter(status="active").order_by("first_name", "last_name"),
            "status_choices": SlaTicket._meta.get_field("status").choices,
            "priority_choices": SlaTicket._meta.get_field("priority").choices,
            "category_choices": SlaTicket._meta.get_field("category").choices,
            "status_filter": status_filter,
            "priority_filter": priority_filter,
            "category_filter": category_filter,
            "contract_filter": contract_filter,
            "assignee_filter": assignee_filter,
            "search_query": query,
            "can_create": _access(request).can("helpdesk", "create"),
        },
    )


@login_required
@module_access_required("helpdesk")
def ticket_create(request):
    access = _access(request)
    if not access.can("helpdesk", "create"):
        messages.error(request, "You do not have permission to log tickets.")
        return redirect("/helpdesk/tickets")
    initial = {}
    if request.GET.get("contract", "").isdigit():
        contract = SlaContract.objects.filter(pk=int(request.GET["contract"])).first()
        if contract:
            initial = {"contract": contract, "project": contract.project_id}
    if request.method == "POST":
        form = TicketForm(request.POST)
        if form.is_valid():
            contract = form.cleaned_data["contract"]
            now = timezone.now()
            setting = services.helpdesk_setting(create=True)
            ticket = form.save(commit=False)
            assignee = form.cleaned_data.get("assigned_to")
            ticket.contract_id = contract.id
            ticket.project_id = (
                form.cleaned_data["project"].id if form.cleaned_data.get("project") else None
            )
            ticket.ticket_number = next_sequence("HD", "pm_sla_tickets", "ticket_number")
            ticket.assigned_to = assignee.id if assignee else None
            if not ticket.assigned_to and setting.auto_assign:
                ticket.assigned_to = services.pick_agent()
            ticket.status = "in_progress" if ticket.assigned_to else "open"
            ticket.response_due_at, ticket.sla_due_at = services.sla_targets(
                contract, form.cleaned_data["priority"], now
            )
            ticket.created_by = request.user.id
            ticket.created_at = now
            if not ticket.requester_name and ticket.project_id:
                project = Project.objects.filter(pk=ticket.project_id).first()
                ticket.requester_name = project.client_name if project else ""
            ticket.save()
            if setting.auto_reply and setting.notify_client:
                services.notify_ticket_received(ticket, request)
            if ticket.assigned_to and setting.notify_client:
                services.notify_agent_assigned(ticket, request)
            HelpdeskEvent.objects.create(
                ticket=ticket,
                kind="created",
                detail=f"Ticket logged from the helpdesk workspace by {request.user.email}.",
                actor_user_id=request.user.id,
            )
            if ticket.project_id:
                log_audit(
                    ticket.project_id,
                    request.user.id,
                    action="ticket_created",
                    new_value=ticket.ticket_number,
                    note=f"Helpdesk ticket “{ticket.title}” logged under {contract.contract_number}.",
                )
            messages.success(request, f"Ticket {ticket.ticket_number} created.")
            return redirect(f"/helpdesk/tickets/{ticket.id}")
        messages.error(request, "Please correct the ticket details.")
    else:
        form = TicketForm(initial=initial)
    return render(
        request,
        "helpdesk/ticket_form.html",
        {"page_title": "New Ticket - SORA ERP", "form": form},
    )


@login_required
@module_access_required("helpdesk")
def ticket_detail(request, ticket_id):
    services.mark_breaches()
    ticket = get_object_or_404(SlaTicket, pk=ticket_id)
    contract = SlaContract.objects.filter(pk=ticket.contract_id).first()
    project = Project.objects.filter(pk=ticket.project_id).first() if ticket.project_id else None
    rows = _decorate_tickets([ticket])
    ticket = rows[0]
    replies = list(TicketReply.objects.filter(ticket_id=ticket.id).select_related("author"))
    employee = _employee_for_user(request.user)
    can_edit = _access(request).can("helpdesk", "edit")
    setting = services.helpdesk_setting()
    events = list(HelpdeskEvent.objects.filter(ticket_id=ticket.id)[:12])
    survey = HelpdeskSurveyResponse.objects.filter(ticket_id=ticket.id).first()
    return render(
        request,
        "helpdesk/ticket_detail.html",
        {
            "page_title": f"{ticket.ticket_number} - SORA ERP",
            "ticket": ticket,
            "contract": contract,
            "project": project,
            "replies": replies,
            "reply_form": ReplyForm(),
            "update_form": TicketUpdateForm(
                initial={
                    "status": ticket.status,
                    "priority": ticket.priority,
                    "assigned_to": ticket.assigned_to,
                }
            ),
            "can_edit": can_edit,
            "can_manage": _can_manage(request),
            "is_my_ticket": bool(employee and ticket.assigned_to == employee.id),
            "setting": setting,
            "events": events,
            "survey": survey,
            "status_url": services.status_url(ticket, request),
            "mail_ready": mail_is_configured("helpdesk"),
        },
    )


@login_required
@module_access_required("helpdesk")
def ticket_reply(request, ticket_id):
    ticket = get_object_or_404(SlaTicket, pk=ticket_id)
    if request.method != "POST":
        return redirect(f"/helpdesk/tickets/{ticket.id}")
    if not _access(request).can("helpdesk", "create"):
        messages.error(request, "You do not have permission to reply to tickets.")
        return redirect(f"/helpdesk/tickets/{ticket.id}")
    form = ReplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Write a message before saving the reply.")
        return redirect(f"/helpdesk/tickets/{ticket.id}")
    now = timezone.now()
    reply = TicketReply.objects.create(
        ticket=ticket,
        author=request.user,
        body=form.cleaned_data["body"],
        channel=form.cleaned_data["channel"],
        is_internal=form.cleaned_data["is_internal"],
    )
    setting = services.helpdesk_setting()
    if not reply.is_internal:
        if ticket.first_response_at is None:
            ticket.first_response_at = now
        if ticket.status == "open":
            ticket.status = "in_progress"
        ticket.save(update_fields=["first_response_at", "status", "updated_at"])
        if (
            form.cleaned_data.get("notify_client")
            and setting is not None
            and setting.notify_client
        ):
            if services.notify_status_update(ticket, request, note=reply.body[:2000]):
                messages.info(request, f"Reply emailed to {ticket.requester_email}.")
            else:
                messages.warning(
                    request,
                    "Reply saved, but the client email could not be sent. Check the helpdesk mailbox identity.",
                )
    if ticket.project_id:
        log_audit(
            ticket.project_id,
            request.user.id,
            action="ticket_reply",
            task_id=None,
            note=("Internal note" if reply.is_internal else "Client reply")
            + f" added to ticket {ticket.ticket_number}.",
        )
    HelpdeskEvent.objects.create(
        ticket=ticket,
        kind="internal_note" if reply.is_internal else "reply",
        detail=f"{'Internal note' if reply.is_internal else 'Reply'} added by {request.user.email}.",
        actor_user_id=request.user.id,
    )
    messages.success(request, "Internal note saved." if reply.is_internal else "Reply added.")
    return redirect(f"/helpdesk/tickets/{ticket.id}")


@login_required
@module_access_required("helpdesk")
def ticket_update(request, ticket_id):
    ticket = get_object_or_404(SlaTicket, pk=ticket_id)
    if request.method != "POST":
        return redirect(f"/helpdesk/tickets/{ticket.id}")
    if not _access(request).can("helpdesk", "edit"):
        messages.error(request, "You do not have permission to update tickets.")
        return redirect(f"/helpdesk/tickets/{ticket.id}")
    form = TicketUpdateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the ticket update.")
        return redirect(f"/helpdesk/tickets/{ticket.id}")
    now = timezone.now()
    setting = services.helpdesk_setting(create=True)
    old_status = ticket.status
    old_assignee = ticket.assigned_to
    ticket.status = form.cleaned_data["status"]
    ticket.priority = form.cleaned_data["priority"]
    assignee = form.cleaned_data.get("assigned_to")
    ticket.assigned_to = assignee.id if assignee else None
    status_changed = ticket.status != old_status
    assignee_changed = ticket.assigned_to != old_assignee
    notify = form.cleaned_data.get("notify_client") and setting.notify_client
    if status_changed and notify and ticket.first_response_at is None:
        ticket.first_response_at = now
    if ticket.status in CLOSED_STATUSES:
        ticket.resolved_at = ticket.resolved_at or now
    else:
        ticket.resolved_at = None
    if ticket.sla_due_at and ticket.sla_due_at < now and ticket.status not in CLOSED_STATUSES:
        ticket.breached = True
    if ticket.response_due_at and ticket.first_response_at is None:
        if ticket.response_due_at < now and ticket.status not in CLOSED_STATUSES:
            ticket.response_breached = True
    elif ticket.first_response_at and ticket.response_due_at:
        if ticket.first_response_at > ticket.response_due_at:
            ticket.response_breached = True
    ticket.save()
    emailed = False
    if notify and status_changed:
        emailed = services.notify_status_update(
            ticket,
            request,
            note=form.cleaned_data.get("note") or "",
        )
    elif notify and assignee_changed and ticket.assigned_to:
        emailed = services.notify_agent_assigned(ticket, request)
    if status_changed and ticket.status in CLOSED_STATUSES and setting.survey_enabled:
        if services.send_survey(ticket, request):
            messages.info(request, "A satisfaction survey was emailed to the requester.")
    if notify and (status_changed or (assignee_changed and ticket.assigned_to)) and not emailed:
        messages.warning(
            request,
            "Ticket updated, but the client email could not be sent. Check the helpdesk mailbox identity.",
        )
    if ticket.project_id:
        changes = [f"status {old_status} → {ticket.status}"]
        if assignee_changed:
            changes.append("assignment changed")
        log_audit(
            ticket.project_id,
            request.user.id,
            action="ticket_updated",
            field_name="status",
            old_value=old_status,
            new_value=ticket.status,
            note=(form.cleaned_data.get("note") or f"Ticket {ticket.ticket_number} updated: " + ", ".join(changes)),
        )
    messages.success(request, "Ticket updated.")
    HelpdeskEvent.objects.create(
        ticket=ticket,
        kind="updated",
        detail=(
            f"Status {old_status} → {ticket.status}; "
            f"assigned to employee #{ticket.assigned_to or '—'}."
        ),
        actor_user_id=request.user.id,
    )
    return redirect(f"/helpdesk/tickets/{ticket.id}")


@login_required
@module_access_required("helpdesk")
def contracts(request):
    rows = []
    for contract in SlaContract.objects.all().order_by("status", "-start_date"):
        open_count = SlaTicket.objects.filter(contract_id=contract.id).exclude(
            status__in=CLOSED_STATUSES
        ).count()
        breached = SlaTicket.objects.filter(contract_id=contract.id, breached=True).exclude(
            status__in=CLOSED_STATUSES
        ).count()
        contract.open_tickets = open_count
        contract.breached_tickets = breached
        rows.append(contract)
    active = [row for row in rows if row.status == "active"]
    return render(
        request,
        "helpdesk/contracts.html",
        {
            "page_title": "SLA Contracts - SORA ERP",
            "contracts": rows,
            "active_count": len(active),
            "monthly_value": _money(sum((row.monthly_value or ZERO for row in active), ZERO)),
            "can_create": _access(request).can("helpdesk", "create"),
        },
    )


@login_required
@module_access_required("helpdesk")
def contract_create(request):
    if not _access(request).can("helpdesk", "create"):
        messages.error(request, "You do not have permission to create SLA contracts.")
        return redirect("/helpdesk/contracts")
    if request.method == "POST":
        form = ContractForm(request.POST)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.contract_number = form.cleaned_data.get(
                "contract_number"
            ) or next_code("SLA", "pm_sla_contracts", "contract_number", width=4)
            contract.project_id = (
                form.cleaned_data["project"].id if form.cleaned_data.get("project") else None
            )
            contract.created_by = request.user.id
            contract.created_at = timezone.now()
            contract.save()
            messages.success(request, f"SLA policy {contract.contract_number} created.")
            return redirect(f"/helpdesk/contracts/{contract.id}")
        messages.error(request, "Please correct the contract details.")
    else:
        form = ContractForm(initial={"status": "active"})
    return render(
        request,
        "helpdesk/contract_form.html",
        {"page_title": "New SLA Policy - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("helpdesk")
def contract_edit(request, contract_id):
    contract = get_object_or_404(SlaContract, pk=contract_id)
    if not _access(request).can("helpdesk", "edit"):
        messages.error(request, "You do not have permission to edit SLA contracts.")
        return redirect(f"/helpdesk/contracts/{contract.id}")
    if request.method == "POST":
        form = ContractForm(request.POST, instance=contract)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.project_id = (
                form.cleaned_data["project"].id if form.cleaned_data.get("project") else None
            )
            contract.save()
            messages.success(request, "SLA policy updated.")
            return redirect(f"/helpdesk/contracts/{contract.id}")
        messages.error(request, "Please correct the contract details.")
    else:
        form = ContractForm(instance=contract)
    return render(
        request,
        "helpdesk/contract_form.html",
        {
            "page_title": f"Edit {contract.contract_number} - SORA ERP",
            "form": form,
            "contract": contract,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("helpdesk")
def contract_detail(request, contract_id):
    services.mark_breaches()
    contract = get_object_or_404(SlaContract, pk=contract_id)
    project = Project.objects.filter(pk=contract.project_id).first() if contract.project_id else None
    ticket_rows = _decorate_tickets(
        SlaTicket.objects.filter(contract_id=contract.id).order_by("-created_at", "-id")[:50]
    )
    checks = list(SlaHealthCheck.objects.filter(contract_id=contract.id).order_by("scheduled_date"))
    employees = field_employee_map({check.performed_by for check in checks if check.performed_by})
    for check in checks:
        check.performed_name = employee_label(employees.get(check.performed_by))
    open_count = SlaTicket.objects.filter(contract_id=contract.id).exclude(
        status__in=CLOSED_STATUSES
    ).count()
    breached_count = SlaTicket.objects.filter(contract_id=contract.id, breached=True).exclude(
        status__in=CLOSED_STATUSES
    ).count()
    return render(
        request,
        "helpdesk/contract_detail.html",
        {
            "page_title": f"{contract.contract_number} - SORA ERP",
            "contract": contract,
            "project": project,
            "tickets": ticket_rows,
            "checks": checks,
            "open_count": open_count,
            "breached_count": breached_count,
            "can_edit": _access(request).can("helpdesk", "edit"),
            "health_form": HealthCheckForm(initial={"contract": contract}),
        },
    )


@login_required
@module_access_required("helpdesk")
def schedule_health_check(request, contract_id):
    contract = get_object_or_404(SlaContract, pk=contract_id)
    if request.method != "POST":
        return redirect(f"/helpdesk/contracts/{contract.id}")
    if not _access(request).can("helpdesk", "create"):
        messages.error(request, "You do not have permission to schedule health checks.")
        return redirect(f"/helpdesk/contracts/{contract.id}")
    form = HealthCheckForm(request.POST)
    if form.is_valid():
        check = form.save(commit=False)
        check.contract_id = contract.id
        check.project_id = contract.project_id
        check.created_at = timezone.now()
        check.save()
        messages.success(request, "Health check scheduled.")
    else:
        messages.error(request, "Please provide a valid date and check type.")
    return redirect(f"/helpdesk/contracts/{contract.id}")


@login_required
@module_access_required("helpdesk")
def health_checks(request):
    if request.method == "POST":
        if not _access(request).can("helpdesk", "create"):
            messages.error(request, "You do not have permission to schedule health checks.")
            return redirect("/helpdesk/health-checks")
        form = HealthCheckForm(request.POST)
        if form.is_valid():
            check = form.save(commit=False)
            check.contract_id = form.cleaned_data["contract"].id
            contract = form.cleaned_data["contract"]
            check.project_id = contract.project_id
            check.created_at = timezone.now()
            check.save()
            messages.success(request, "Health check scheduled.")
            return redirect("/helpdesk/health-checks")
        messages.error(request, "Please provide a valid contract, date and check type.")
    else:
        form = HealthCheckForm(initial={"scheduled_date": timezone.localdate()})
    queryset = SlaHealthCheck.objects.all().order_by("scheduled_date", "id")
    status_filter = request.GET.get("status") or ""
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    checks = list(queryset)
    contracts = _contract_map({check.contract_id for check in checks})
    employees = field_employee_map({check.performed_by for check in checks if check.performed_by})
    for check in checks:
        contract = contracts.get(check.contract_id)
        check.contract_number = contract.contract_number if contract else f"Contract #{check.contract_id}"
        check.performed_name = employee_label(employees.get(check.performed_by))
        check.is_overdue = check.status == "scheduled" and check.scheduled_date < timezone.localdate()
    return render(
        request,
        "helpdesk/health_checks.html",
        {
            "page_title": "Health Checks - SORA ERP",
            "checks": checks,
            "status_filter": status_filter,
            "status_choices": SlaHealthCheck._meta.get_field("status").choices,
            "can_create": _access(request).can("helpdesk", "create"),
            "can_edit": _access(request).can("helpdesk", "edit"),
            "form": form,
            "agents": Employee.objects.filter(status="active").order_by("first_name", "last_name"),
        },
    )


@login_required
@module_access_required("helpdesk")
def health_check_complete(request, check_id):
    check = get_object_or_404(SlaHealthCheck, pk=check_id)
    if request.method != "POST":
        return redirect("/helpdesk/health-checks")
    if not _access(request).can("helpdesk", "edit"):
        messages.error(request, "You do not have permission to complete health checks.")
        return redirect("/helpdesk/health-checks")
    form = HealthCheckCompleteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Add findings before completing the health check.")
        return redirect("/helpdesk/health-checks")
    check.findings = form.cleaned_data["findings"]
    employee = form.cleaned_data.get("performed_by")
    check.performed_by = employee.id if employee else None
    check.status = "completed"
    check.completed_at = timezone.now()
    check.save()
    if check.project_id:
        log_audit(
            check.project_id,
            request.user.id,
            action="health_check_completed",
            note=f"Preventive health check #{check.id} completed.",
        )
    messages.success(request, "Health check completed and findings recorded.")
    return redirect("/helpdesk/health-checks")

# ---------------------------------------------------------------------------
# Automation settings, routing rules and mailbox polling
# ---------------------------------------------------------------------------
def _agent_rows():
    rows = list(HelpdeskAgent.objects.order_by("id"))
    employees = field_employee_map({row.employee_id for row in rows})
    for row in rows:
        employee = employees.get(row.employee_id)
        row.name = employee_label(employee) if employee else f"Employee #{row.employee_id}"
        row.email = getattr(employee, "email", "") or ""
        row.is_current = bool(employee and employee.status == "active")
    return rows


def _settings_context(setting, *, mailbox_form=None, survey_form=None, policy_form=None):
    policies = services.ensure_sla_policies()
    policy_form = policy_form or SlaPolicyForm(policies=policies)
    labels = dict(PRIORITY_CHOICES)
    policy_rows = [
        {
            "priority": priority,
            "label": labels[priority],
            "response": policy_form[f"{priority}_response"],
            "resolution": policy_form[f"{priority}_resolution"],
            "policy": policies.get(priority),
        }
        for priority in POLICY_ORDER
    ]
    return {
        "page_title": "Helpdesk Automation - SORA ERP",
        "setting": setting,
        "mailbox_form": mailbox_form or HelpdeskMailboxForm(instance=setting),
        "survey_form": survey_form or HelpdeskSurveyForm(instance=setting),
        "policy_form": policy_form,
        "policy_rows": policy_rows,
        "agents": _agent_rows(),
        "rules": list(HelpdeskRoutingRule.objects.all()),
        "recent_inbound": list(
            HelpdeskInboundMessage.objects.select_related("ticket")[:8]
        ),
        "can_manage": True,
    }


@login_required
@module_access_required("helpdesk")
def settings_page(request):
    guard = _settings_guard(request)
    if guard:
        return guard
    setting = services.helpdesk_setting(create=True)
    services.ensure_agent_pool()
    return render(request, "helpdesk/settings.html", _settings_context(setting))


@login_required
@module_access_required("helpdesk")
def settings_mailbox(request):
    guard = _settings_guard(request)
    if guard:
        return guard
    setting = services.helpdesk_setting(create=True)
    if request.method != "POST":
        return redirect("/helpdesk/settings#mailbox")
    form = HelpdeskMailboxForm(request.POST, instance=setting)
    if form.is_valid():
        setting = form.save(commit=False)
        setting.updated_by = request.user.id
        setting.save()
        messages.success(request, "Mailbox and notification settings saved.")
        return redirect("/helpdesk/settings#mailbox")
    messages.error(request, "Please correct the mailbox settings.")
    return render(
        request,
        "helpdesk/settings.html",
        _settings_context(setting, mailbox_form=form),
    )


@login_required
@module_access_required("helpdesk")
def settings_sla(request):
    guard = _settings_guard(request)
    if guard:
        return guard
    setting = services.helpdesk_setting(create=True)
    if request.method != "POST":
        return redirect("/helpdesk/settings#sla")
    policies = services.ensure_sla_policies()
    form = SlaPolicyForm(request.POST, policies=policies)
    if form.is_valid():
        services.save_sla_policies(form.policy_values())
        messages.success(request, "Priority SLA targets saved.")
        return redirect("/helpdesk/settings#sla")
    messages.error(request, "Please correct the SLA targets.")
    return render(
        request,
        "helpdesk/settings.html",
        _settings_context(setting, policy_form=form),
    )


@login_required
@module_access_required("helpdesk")
def settings_survey(request):
    guard = _settings_guard(request)
    if guard:
        return guard
    setting = services.helpdesk_setting(create=True)
    if request.method != "POST":
        return redirect("/helpdesk/settings#survey")
    form = HelpdeskSurveyForm(request.POST, instance=setting)
    if form.is_valid():
        setting = form.save(commit=False)
        setting.updated_by = request.user.id
        setting.save()
        messages.success(request, "Client survey saved.")
        return redirect("/helpdesk/settings#survey")
    messages.error(request, "Please correct the survey settings.")
    return render(
        request,
        "helpdesk/settings.html",
        _settings_context(setting, survey_form=form),
    )


@login_required
@module_access_required("helpdesk")
def settings_agents(request):
    guard = _settings_guard(request)
    if guard:
        return guard
    if request.method != "POST":
        return redirect("/helpdesk/settings#agents")
    services.ensure_agent_pool()
    updated = 0
    for agent in HelpdeskAgent.objects.all():
        active = request.POST.get(f"agent_{agent.id}") == "on"
        if agent.is_active != active:
            agent.is_active = active
            agent.save(update_fields=["is_active", "updated_at"])
            updated += 1
    messages.success(
        request,
        f"Agent pool updated ({updated} change{'s' if updated != 1 else ''}).",
    )
    return redirect("/helpdesk/settings#agents")


@login_required
@module_access_required("helpdesk")
def settings_poll(request):
    guard = _settings_guard(request)
    if guard:
        return guard
    if request.method != "POST":
        return redirect("/helpdesk/settings#mailbox")
    setting = services.helpdesk_setting(create=True)
    if setting.mailbox_identity is None or not setting.mailbox_active:
        messages.error(request, "Link and enable a mailbox before polling.")
        return redirect("/helpdesk/settings#mailbox")
    summary = services.poll_mailbox(setting)
    if summary["errors"]:
        messages.error(
            request,
            f"Mailbox poll failed: {setting.last_poll_error[:300]}",
        )
    else:
        messages.success(
            request,
            "Mailbox checked: "
            f"{summary['created']} new ticket(s), {summary['processed']} message(s) processed, "
            f"{summary['duplicates']} duplicate(s), {summary['ignored']} ignored.",
        )
    return redirect("/helpdesk/settings#mailbox")


@login_required
@module_access_required("helpdesk")
def routing_rules(request):
    guard = _settings_guard(request)
    if guard:
        return guard
    rules = list(HelpdeskRoutingRule.objects.all())
    employees = field_employee_map({rule.assignee_id for rule in rules if rule.assignee_id})
    for rule in rules:
        rule.assignee_name = (
            employee_label(employees.get(rule.assignee_id))
            if rule.assignee_id
            else ""
        )
    return render(
        request,
        "helpdesk/routing_rules.html",
        {
            "page_title": "Routing Rules - SORA ERP",
            "rules": rules,
            "can_manage": True,
        },
    )


@login_required
@module_access_required("helpdesk")
def routing_rule_create(request):
    guard = _settings_guard(request)
    if guard:
        return guard
    if request.method == "POST":
        form = RoutingRuleForm(request.POST)
        if form.is_valid():
            rule = form.save(commit=False)
            assignee = form.cleaned_data.get("assignee")
            rule.assignee_id = assignee.id if assignee else None
            rule.save()
            messages.success(request, f"Routing rule “{rule.name}” created.")
            return redirect("/helpdesk/settings/routing")
        messages.error(request, "Please correct the routing rule.")
    else:
        form = RoutingRuleForm(
            initial={"order": 10, "is_active": True, "stop_processing": True}
        )
    return render(
        request,
        "helpdesk/routing_rule_form.html",
        {"page_title": "New Routing Rule - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("helpdesk")
def routing_rule_edit(request, rule_id):
    guard = _settings_guard(request)
    if guard:
        return guard
    rule = get_object_or_404(HelpdeskRoutingRule, pk=rule_id)
    if request.method == "POST":
        form = RoutingRuleForm(request.POST, instance=rule)
        if form.is_valid():
            rule = form.save(commit=False)
            assignee = form.cleaned_data.get("assignee")
            rule.assignee_id = assignee.id if assignee else None
            rule.save()
            messages.success(request, "Routing rule updated.")
            return redirect("/helpdesk/settings/routing")
        messages.error(request, "Please correct the routing rule.")
    else:
        form = RoutingRuleForm(instance=rule)
    return render(
        request,
        "helpdesk/routing_rule_form.html",
        {
            "page_title": "Edit Routing Rule - SORA ERP",
            "form": form,
            "rule": rule,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("helpdesk")
def routing_rule_delete(request, rule_id):
    guard = _settings_guard(request)
    if guard:
        return guard
    rule = get_object_or_404(HelpdeskRoutingRule, pk=rule_id)
    if request.method == "POST":
        name = rule.name
        rule.delete()
        messages.success(request, f"Routing rule “{name}” deleted.")
    return redirect("/helpdesk/settings/routing")


@login_required
@module_access_required("helpdesk")
def ticket_notify_client(request, ticket_id):
    ticket = get_object_or_404(SlaTicket, pk=ticket_id)
    if request.method != "POST":
        return redirect(f"/helpdesk/tickets/{ticket.id}")
    if not _access(request).can("helpdesk", "edit"):
        messages.error(request, "You do not have permission to email the requester.")
        return redirect(f"/helpdesk/tickets/{ticket.id}")
    action = request.POST.get("action") or "status"
    if action == "survey":
        sent = services.send_survey(ticket, request, force=True)
        if sent:
            messages.success(request, f"Survey emailed to {ticket.requester_email}.")
        else:
            messages.warning(
                request,
                "The survey could not be sent. Resolve the ticket first and check the mailbox identity.",
            )
    else:
        sent = services.notify_status_update(ticket, request)
        if sent:
            messages.success(request, f"Status email sent to {ticket.requester_email}.")
        else:
            messages.warning(
                request,
                "The status email could not be sent. Check the requester email and mailbox identity.",
            )
    return redirect(f"/helpdesk/tickets/{ticket.id}")


# ---------------------------------------------------------------------------
# Public client portal (signed, expiring links; no staff login required)
# ---------------------------------------------------------------------------
def _client_agent_name(ticket) -> str:
    if not ticket.assigned_to:
        return "Our support team"
    employee = Employee.objects.filter(pk=ticket.assigned_to).first()
    return employee_label(employee) if employee else "Our support team"


@require_http_methods(["GET", "POST"])
def client_status(request, ticket_id, token):
    try:
        ticket = services.load_client_ticket(ticket_id, token)
    except signing.BadSignature:
        return render(
            request,
            "helpdesk/client_invalid.html",
            {"ticket_id": ticket_id},
            status=400,
        )

    with tenant_context(ticket.tenant):
        request.tenant = ticket.tenant
        setting = services.helpdesk_setting(create=True)
        ticket = SlaTicket.objects.get(pk=ticket.pk)
        survey = HelpdeskSurveyResponse.objects.filter(ticket_id=ticket.id).first()
        closed = ticket.status in CLOSED_STATUSES
        can_survey = bool(closed and setting.survey_enabled and survey is None)

        if request.method == "POST":
            action = request.POST.get("action")
            if action == "confirm":
                if not closed:
                    return redirect(f"{request.path}?notice=active")
                services.confirm_resolution(ticket, request)
                return redirect(f"{request.path}?confirmed=1")
            if action == "survey":
                if not can_survey:
                    return redirect(f"{request.path}?survey=unavailable")
                form = SurveyResponseForm(request.POST)
                if not form.is_valid():
                    return redirect(f"{request.path}?survey=invalid")
                comment = ""
                if setting.survey_comment_enabled:
                    comment = form.cleaned_data.get("comment") or ""
                if services.record_survey(
                    ticket, form.cleaned_data["rating"], comment, request
                ):
                    return redirect(f"{request.path}?survey=done")
                return redirect(f"{request.path}?survey=unavailable")
            return redirect(request.path)

        ticket = SlaTicket.objects.get(pk=ticket.pk)
        replies = list(
            TicketReply.objects.filter(ticket_id=ticket.id, is_internal=False).order_by(
                "created_at", "id"
            )
        )
        notice = ""
        notice_kind = ""
        if request.GET.get("confirmed"):
            notice = "Thank you — your confirmation has been recorded."
            notice_kind = "success"
        elif request.GET.get("survey") == "done":
            notice = setting.survey_thanks
            notice_kind = "success"
        elif request.GET.get("survey") == "invalid":
            notice = "Please choose a rating between 1 and 5."
            notice_kind = "error"
        elif request.GET.get("survey") == "unavailable":
            notice = "The survey is no longer available for this ticket."
            notice_kind = "error"
        elif request.GET.get("notice") == "active":
            notice = (
                "This ticket is still being worked on. Our team will update you soon."
            )
            notice_kind = "info"

        context = {
            "ticket": ticket,
            "replies": replies,
            "setting": setting,
            "survey": survey,
            "survey_form": SurveyResponseForm(),
            "can_survey": can_survey,
            "closed": closed,
            "can_confirm": bool(closed and not ticket.client_confirmed_at),
            "agent_name": _client_agent_name(ticket),
            "status_label": ticket.get_status_display(),
            "notice": notice,
            "notice_kind": notice_kind,
            "focus_survey": can_survey and request.GET.get("survey") == "1",
            "company_name": get_company_settings().get("company_name") or "SORA ERP",
        }
        return render(request, "helpdesk/client_status.html", context)
