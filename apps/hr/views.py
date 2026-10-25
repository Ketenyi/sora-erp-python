"""HR module views: employees, leave, attendance, payroll and engagement."""

from __future__ import annotations

import calendar
import csv
import io
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import (
    Approver,
    Attendance,
    Candidate,
    Department,
    Employee,
    EmployeeDocument,
    EmployeeFeedback,
    EmployeeMovement,
    EmployeeRecognition,
    EmployeeSkill,
    EmployeeTraining,
    Goal,
    Interview,
    JobPosting,
    LeaveBalance,
    LeaveRequest,
    LeaveTransaction,
    LeaveType,
    OvertimeRequest,
    PayrollItem,
    PayrollPeriod,
    PerformanceReview,
    Position,
    RecognitionBadge,
    Skill,
    SuccessionPlan,
    TrainingProgram,
    User,
    WellnessCheckin,
)
from apps.core.permissions import get_access, module_access_required
from apps.core.services import get_company_settings, get_currency_symbol, next_code, to_decimal
from apps.core.ui import csv_response, paginate
from apps.core.uploads import save_tenant_upload
from apps.hr.forms import (
    ApproverForm,
    CandidateForm,
    DepartmentForm,
    EmployeeForm,
    FeedbackForm,
    GoalForm,
    InterviewFeedbackForm,
    InterviewForm,
    JobPostingForm,
    LeaveRequestForm,
    LeaveTypeForm,
    OvertimeRequestForm,
    PayrollPeriodForm,
    PerformanceReviewForm,
    PositionForm,
    RecognitionGiveForm,
    SuccessionPlanForm,
    TrainingProgramForm,
    WellnessCheckinForm,
)

PAGE_SIZE = 15
WORK_START = time(9, 0)
WORK_END = time(17, 0)
STANDARD_HOURS = Decimal("8")
CANDIDATE_TRANSITIONS = {
    "new": {"screening", "interview", "rejected", "withdrawn"},
    "screening": {"interview", "assessment", "rejected", "withdrawn"},
    "interview": {"assessment", "offered", "rejected", "withdrawn"},
    "assessment": {"offered", "rejected", "withdrawn"},
    "offered": {"hired", "rejected", "withdrawn"},
    "hired": set(),
    "rejected": {"new"},
    "withdrawn": {"new"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _employee_for_user(user):
    employee_id = getattr(user, "employee_id", None)
    employee = None
    if employee_id:
        employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        employee = Employee.objects.filter(email__iexact=user.email).first()
        if employee is not None and employee_id != employee.id:
            user.employee_id = employee.id
            user.save(update_fields=["employee_id"])
    return employee


def _employees_map(ids=None):
    queryset = Employee.objects.all()
    if ids is not None:
        queryset = queryset.filter(pk__in={i for i in ids if i})
    return {employee.id: employee for employee in queryset}


def _departments_map(ids=None):
    queryset = Department.objects.all()
    if ids is not None:
        queryset = queryset.filter(pk__in={i for i in ids if i})
    return {department.id: department for department in queryset}


def _positions_map(ids=None):
    queryset = Position.objects.all()
    if ids is not None:
        queryset = queryset.filter(pk__in={i for i in ids if i})
    return {position.id: position for position in queryset}


def _leave_types_map():
    return {leave_type.id: leave_type for leave_type in LeaveType.objects.all()}


def _hr_admin_required(request):
    access = get_access(request.user)
    if access.has_grant("hr"):
        allowed = access.can("hr", "edit")
    else:
        allowed = access.is_admin or access.is_hr_admin
    if not allowed:
        raise PermissionDenied("HR administrator access required.")
    return access


def _hr_staff(request):
    access = get_access(request.user)
    if access.has_grant("hr"):
        allowed = access.can("hr", "view")
    else:
        allowed = access.is_admin or access.is_hr_admin or access.is_hr_viewer
    if not allowed:
        raise PermissionDenied("HR access required.")
    return access


def _require_employee(request):
    employee = _employee_for_user(request.user)
    if employee is None:
        messages.error(request, "Your user account is not linked to an employee record.")
    return employee


def _next_employee_code():
    year = timezone.localdate().year
    prefix = f"EMP-{year}-"
    last = (
        Employee.objects.filter(employee_code__startswith=prefix)
        .order_by("-employee_code")
        .values_list("employee_code", flat=True)
        .first()
    )
    number = 1
    if last:
        try:
            number = int(last.rsplit("-", 1)[-1]) + 1
        except (ValueError, IndexError):
            number = 1
    return f"{prefix}{number:04d}"


def _ensure_leave_balances(employee, year=None):
    year = year or timezone.localdate().year
    created = []
    for leave_type in LeaveType.objects.filter(status="active"):
        balance, was_created = LeaveBalance.objects.get_or_create(
            employee_id=employee.id,
            leave_type_id=leave_type.id,
            year=year,
            defaults={
                "total_days": Decimal(leave_type.days_allowed or 0),
                "total_earned": Decimal(leave_type.days_allowed or 0),
                "used_days": Decimal("0"),
                "remaining_days": Decimal(leave_type.days_allowed or 0),
                "pending_days": Decimal("0"),
                "carried_over": Decimal("0"),
                "adjusted_days": Decimal("0"),
                "created_at": timezone.now(),
            },
        )
        if was_created:
            created.append(balance)
    return created


def _weekday_count(start: date, end: date) -> int:
    total = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            total += 1
        current += timedelta(days=1)
    return total


def _working_days(start: date, end: date) -> int:
    return _weekday_count(start, end)


def _overlapping_leaves(employee_id, start, end, exclude_id=None):
    queryset = LeaveRequest.objects.filter(
        employee_id=employee_id,
        status__in=["pending", "approved"],
        start_date__lte=end,
        end_date__gte=start,
    )
    if exclude_id:
        queryset = queryset.exclude(pk=exclude_id)
    return queryset


def _get_balance(employee_id, leave_type_id, year):
    return LeaveBalance.objects.filter(
        employee_id=employee_id, leave_type_id=leave_type_id, year=year
    ).first()


def _can_approve_leave(user, leave_request):
    access = get_access(user)
    if access.is_admin or access.is_hr_admin or access.can("hr", "approve"):
        return True
    employee = _employee_for_user(user)
    if employee is None:
        return False
    target = Employee.objects.filter(pk=leave_request.employee_id).first()
    if target is None:
        return False
    if target.reports_to == employee.id:
        return True
    query = Q(department_id__isnull=True)
    if target.department_id:
        query |= Q(department_id=target.department_id)
    return Approver.objects.filter(
        query,
        employee_id=employee.id,
        approver_type__in=["leave", "all"],
        status="active",
    ).exists()


def _can_approve_overtime(user, overtime):
    access = get_access(user)
    if access.is_admin or access.is_hr_admin or access.can("hr", "approve"):
        return True
    employee = _employee_for_user(user)
    if employee is None:
        return False
    target = Employee.objects.filter(pk=overtime.employee_id).first()
    if target is None:
        return False
    if target.reports_to == employee.id:
        return True
    return Approver.objects.filter(
        employee_id=employee.id,
        approver_type__in=["overtime", "all"],
        status="active",
    ).exists()


def _parse_amounts(raw):
    if not raw:
        return []
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [{"name": key, "amount": value} for key, value in data.items()]
    if not isinstance(data, list):
        return []
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("label") or "Item").strip()
        amount = to_decimal(item.get("amount") or item.get("value") or 0)
        if amount:
            rows.append({"name": name, "amount": amount})
    return rows


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _paye(taxable: Decimal) -> Decimal:
    bands = [
        (Decimal("24000"), Decimal("0.10")),
        (Decimal("8333"), Decimal("0.25")),
        (Decimal("467667"), Decimal("0.30")),
        (Decimal("300000"), Decimal("0.325")),
        (None, Decimal("0.35")),
    ]
    remaining = max(taxable, Decimal("0"))
    tax = Decimal("0")
    for width, rate in bands:
        if remaining <= 0:
            break
        portion = remaining if width is None else min(remaining, width)
        tax += portion * rate
        remaining -= portion
    return _money(max(Decimal("0"), tax - Decimal("2400")))


def _nssf(basic: Decimal) -> Decimal:
    pensionable = min(max(basic, Decimal("0")), Decimal("72000"))
    return _money(pensionable * Decimal("0.06"))


def _nhif(gross: Decimal) -> Decimal:
    bands = [
        (5999, 150),
        (7999, 300),
        (11999, 400),
        (14999, 500),
        (19999, 600),
        (24999, 750),
        (29999, 850),
        (34999, 900),
        (39999, 950),
        (44999, 1000),
        (49999, 1100),
        (59999, 1200),
        (69999, 1300),
        (79999, 1400),
        (89999, 1500),
        (99999, 1600),
    ]
    value = float(gross)
    for ceiling, amount in bands:
        if value <= ceiling:
            return Decimal(amount)
    return Decimal("1700")


def _amount_in_words(amount: Decimal) -> str:
    units = [
        "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen",
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def below_thousand(number: int) -> str:
        parts = []
        if number >= 100:
            parts.append(f"{units[number // 100]} Hundred")
            number %= 100
        if number >= 20:
            word = tens[number // 10]
            if number % 10:
                word += f"-{units[number % 10]}"
            parts.append(word)
        elif number:
            parts.append(units[number])
        return " ".join(parts)

    def group(number: int) -> str:
        if number == 0:
            return "Zero"
        chunks = []
        scale = [(1_000_000_000, "Billion"), (1_000_000, "Million"), (1_000, "Thousand"), (1, "")]
        for value, label in scale:
            if number >= value:
                chunk = number // value
                number %= value
                text = below_thousand(chunk)
                chunks.append(f"{text} {label}".strip())
        return " ".join(chunks)

    amount = Decimal(amount or 0)
    shillings = int(amount)
    cents = int((amount - shillings) * 100)
    text = f"{group(shillings)} Shillings"
    if cents:
        text += f" and {below_thousand(cents)} Cents"
    return text + " Only"


def _leave_row_employees(leaves):
    mapping = _employees_map([leave.employee_id for leave in leaves])
    for leave in leaves:
        employee = mapping.get(leave.employee_id)
        leave.employee_name = (
            f"{employee.first_name} {employee.last_name}" if employee else f"Employee #{leave.employee_id}"
        )
    return leaves


def _attendance_for_user(user):
    employee = _employee_for_user(user)
    if employee is None:
        return None, None
    record = Attendance.objects.filter(
        employee_id=employee.id, date=timezone.localdate()
    ).first()
    return employee, record


def _accepted_report_types():
    return {
        "employees": "Employees",
        "leave": "Leave",
        "attendance": "Attendance",
        "performance": "Performance",
        "recruitment": "Recruitment",
        "payroll": "Payroll",
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def dashboard(request):
    access = get_access(request.user)
    if access.is_employee and not access.is_hr_viewer and not access.is_hr_admin:
        return redirect("/hr/employee/dashboard")

    today = timezone.localdate()
    active_employees = Employee.objects.filter(
        status="active", employment_status__in=["active", "probation", "on_leave"]
    )
    on_leave_ids = (
        LeaveRequest.objects.filter(
            status="approved", start_date__lte=today, end_date__gte=today
        )
        .values_list("employee_id", flat=True)
        .distinct()
    )
    stats = {
        "employees": active_employees.count(),
        "departments": Department.objects.filter(status="active").count(),
        "pending_leave": LeaveRequest.objects.filter(status="pending").count(),
        "on_leave": len(set(on_leave_ids)),
        "open_positions": JobPosting.objects.filter(status="published").count(),
        "new_hires": Employee.objects.filter(
            hire_date__year=today.year, hire_date__month=today.month
        ).count(),
    }
    birthdays = list(
        active_employees.filter(date_of_birth__month=today.month)
        .order_by("date_of_birth")
        .only("id", "first_name", "last_name", "date_of_birth", "job_title")[:6]
    )
    anniversaries = []
    for employee in active_employees.filter(
        hire_date__month=today.month, hire_date__year__lt=today.year
    ).order_by("hire_date")[:6]:
        employee.years_of_service = today.year - employee.hire_date.year
        anniversaries.append(employee)

    recent_leave = list(
        LeaveRequest.objects.order_by("-created_at").only(
            "id", "employee_id", "leave_type_id", "start_date", "end_date", "status", "duration_days"
        )[:8]
    )
    _leave_row_employees(recent_leave)
    leave_types = _leave_types_map()
    for leave in recent_leave:
        leave.type_name = getattr(leave_types.get(leave.leave_type_id), "leave_name", "Leave")

    pending_leave = list(
        LeaveRequest.objects.filter(status="pending").order_by("-created_at")[:6]
    )
    _leave_row_employees(pending_leave)

    return render(
        request,
        "hr/dashboard.html",
        {
            "page_title": "HR Dashboard - SORA ERP",
            "stats": stats,
            "birthdays": birthdays,
            "anniversaries": anniversaries,
            "recent_leave": recent_leave,
            "pending_leave": pending_leave,
            "access": access,
            "today": today,
        },
    )


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def employee_list(request):
    access = _hr_staff(request)
    query = (request.GET.get("q") or "").strip()
    department_filter = request.GET.get("department") or ""
    status_filter = request.GET.get("status") or ""
    queryset = Employee.objects.all()
    if query:
        queryset = queryset.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(employee_code__icontains=query)
            | Q(email__icontains=query)
            | Q(job_title__icontains=query)
        )
    if department_filter:
        queryset = queryset.filter(department_id=department_filter)
    if status_filter:
        queryset = queryset.filter(employment_status=status_filter)
    queryset = queryset.order_by("first_name", "last_name")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    departments = _departments_map([employee.department_id for employee in page_obj])
    positions = _positions_map([employee.position_id for employee in page_obj])
    today = timezone.localdate()
    stats = {
        "total": Employee.objects.count(),
        "active": Employee.objects.filter(
            status="active", employment_status__in=["active", "probation", "on_leave"]
        ).count(),
        "on_leave": LeaveRequest.objects.filter(
            status="approved", start_date__lte=today, end_date__gte=today
        )
        .values("employee_id")
        .distinct()
        .count(),
        "new_hires": Employee.objects.filter(
            hire_date__year=today.year, hire_date__month=today.month
        ).count(),
    }
    return render(
        request,
        "hr/employees.html",
        {
            "page_title": "Employees - SORA ERP",
            "page_obj": page_obj,
            "employees": page_obj.object_list,
            "departments": sorted(_departments_map().values(), key=lambda d: d.department_name),
            "positions": positions,
            "department_map": departments,
            "search_query": query,
            "department_filter": department_filter,
            "status_filter": status_filter,
            "status_choices": Employee._meta.get_field("employment_status").choices,
            "stats": stats,
            "can_manage": access.is_admin or access.is_hr_admin,
            "today": today,
        },
    )


@login_required
@module_access_required("hr")
def employee_create(request):
    _hr_admin_required(request)
    if request.method == "POST":
        form = EmployeeForm(request.POST)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.employee_code = _next_employee_code()
            employee.created_by = request.user.id
            employee.created_at = timezone.now()
            employee.status = employee.status or "active"
            employee.save()
            _ensure_leave_balances(employee)
            messages.success(
                request, f"Employee {employee.first_name} {employee.last_name} created ({employee.employee_code})."
            )
            return redirect(f"/hr/employees/view/{employee.id}")
    else:
        form = EmployeeForm(initial={"hire_date": timezone.localdate(), "employment_status": "active"})
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Employee - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("hr")
def employee_view(request, id):
    _hr_staff(request)
    employee = get_object_or_404(Employee, pk=id)
    department = _departments_map([employee.department_id]).get(employee.department_id)
    position = _positions_map([employee.position_id]).get(employee.position_id)
    manager = _employees_map([employee.reports_to]).get(employee.reports_to)
    documents = list(EmployeeDocument.objects.filter(employee_id=employee.id).order_by("-created_at")[:10])
    skills = list(EmployeeSkill.objects.filter(employee_id=employee.id)[:20])
    skill_map = {skill.id: skill for skill in Skill.objects.all()}
    for row in skills:
        row.skill_name = getattr(skill_map.get(row.skill_id), "skill_name", f"Skill #{row.skill_id}")
    movements = list(
        EmployeeMovement.objects.filter(employee_id=employee.id).order_by("-effective_date")[:10]
    )
    current_year = timezone.localdate().year
    _ensure_leave_balances(employee, current_year)
    balances = list(LeaveBalance.objects.filter(employee_id=employee.id, year=current_year))
    leave_types = _leave_types_map()
    for balance in balances:
        leave_type = leave_types.get(balance.leave_type_id)
        balance.type_name = getattr(leave_type, "leave_name", "Leave")
        balance.color = getattr(leave_type, "color", "#FFD700")
    reports = Employee.objects.filter(reports_to=employee.id).only(
        "id", "first_name", "last_name", "job_title"
    )[:10]
    return render(
        request,
        "hr/employee_view.html",
        {
            "page_title": f"{employee.first_name} {employee.last_name} - SORA ERP",
            "employee": employee,
            "department": department,
            "position": position,
            "manager": manager,
            "documents": documents,
            "skills": skills,
            "movements": movements,
            "balances": balances,
            "reports": reports,
            "can_manage": get_access(request.user).is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def employee_edit(request, id):
    _hr_admin_required(request)
    employee = get_object_or_404(Employee, pk=id)
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        if form.is_valid():
            employee = form.save()
            _ensure_leave_balances(employee)
            messages.success(request, "Employee profile updated.")
            return redirect(f"/hr/employees/view/{employee.id}")
    else:
        form = EmployeeForm(instance=employee)
    return render(
        request,
        "hr/form_page.html",
        {
            "page_title": "Edit Employee - SORA ERP",
            "form": form,
            "employee": employee,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("hr")
def employee_delete(request, id):
    _hr_admin_required(request)
    employee = get_object_or_404(Employee, pk=id)
    if request.method == "POST":
        if employee.id == getattr(_employee_for_user(request.user), "id", None):
            messages.error(request, "You cannot deactivate your own employee record.")
        else:
            employee.status = "inactive"
            employee.employment_status = "terminated"
            employee.termination_date = timezone.localdate()
            employee.updated_at = timezone.now()
            employee.save(update_fields=["status", "employment_status", "termination_date", "updated_at"])
            messages.success(request, "Employee deactivated. Historical records were retained.")
    return redirect(f"/hr/employees/view/{employee.id}")


@login_required
@module_access_required("hr")
def employee_export(request):
    _hr_staff(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="employees.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "Employee code", "First name", "Last name", "Email", "Phone", "Department",
            "Job title", "Employment status", "Hire date", "Salary", "Status",
        ]
    )
    departments = _departments_map()
    for employee in Employee.objects.order_by("first_name", "last_name"):
        department = departments.get(employee.department_id)
        writer.writerow(
            [
                employee.employee_code,
                employee.first_name,
                employee.last_name,
                employee.email,
                employee.phone or "",
                getattr(department, "department_name", ""),
                employee.job_title or "",
                employee.get_employment_status_display() or "",
                employee.hire_date,
                employee.salary or "",
                employee.get_status_display() or "",
            ]
        )
    return response


# ---------------------------------------------------------------------------
# Departments and positions
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def department_list(request):
    access = _hr_staff(request)
    query = (request.GET.get("q") or "").strip()
    status_filter = request.GET.get("status") or ""
    queryset = Department.objects.all()
    if query:
        queryset = queryset.filter(
            Q(department_name__icontains=query) | Q(department_code__icontains=query)
        )
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    departments = list(queryset.order_by("department_name"))
    counts = {
        row["department_id"]: row["count"]
        for row in Employee.objects.exclude(employment_status__in=["terminated", "resigned", "retired"])
        .values("department_id")
        .annotate(count=Count("id"))
    }
    managers = _employees_map([department.manager_id for department in departments])
    parent_map = {department.id: department.department_name for department in Department.objects.all()}
    for department in departments:
        department.employee_count = counts.get(department.id, 0)
        department.manager_name = (
            f"{managers[department.manager_id].first_name} {managers[department.manager_id].last_name}"
            if department.manager_id in managers
            else "—"
        )
        department.parent_name = parent_map.get(department.parent_department_id, "—")
    return render(
        request,
        "hr/departments.html",
        {
            "page_title": "Departments - SORA ERP",
            "departments": departments,
            "search_query": query,
            "status_filter": status_filter,
            "can_manage": access.is_admin or access.is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def department_create(request):
    _hr_admin_required(request)
    if request.method == "POST":
        form = DepartmentForm(request.POST)
        if form.is_valid():
            department = form.save(commit=False)
            department.created_at = timezone.now()
            department.save()
            messages.success(request, "Department created.")
            return redirect("/hr/departments")
    else:
        form = DepartmentForm()
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Department - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("hr")
def department_edit(request, id):
    _hr_admin_required(request)
    department = get_object_or_404(Department, pk=id)
    if request.method == "POST":
        form = DepartmentForm(request.POST, instance=department)
        if form.is_valid():
            department = form.save(commit=False)
            department.updated_at = timezone.now()
            department.save()
            messages.success(request, "Department updated.")
            return redirect("/hr/departments")
    else:
        form = DepartmentForm(instance=department)
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "Edit Department - SORA ERP", "form": form, "department": department, "is_edit": True},
    )


@login_required
@module_access_required("hr")
def department_delete(request, id):
    _hr_admin_required(request)
    department = get_object_or_404(Department, pk=id)
    if request.method == "POST":
        if Employee.objects.filter(department_id=department.id).exclude(
            employment_status__in=["terminated", "resigned", "retired"]
        ).exists():
            messages.error(request, "Reassign active employees before deleting this department.")
        elif Department.objects.filter(parent_department_id=department.id).exists():
            messages.error(request, "Move or delete child departments first.")
        else:
            department.delete()
            messages.success(request, "Department deleted.")
    return redirect("/hr/departments")


@login_required
@module_access_required("hr")
def position_list(request):
    access = _hr_staff(request)
    query = (request.GET.get("q") or "").strip()
    status_filter = request.GET.get("status") or ""
    queryset = Position.objects.all()
    if query:
        queryset = queryset.filter(
            Q(position_name__icontains=query)
            | Q(position_code__icontains=query)
            | Q(job_family__icontains=query)
        )
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    positions = list(queryset.order_by("position_name"))
    counts = {
        row["position_id"]: row["count"]
        for row in Employee.objects.exclude(employment_status__in=["terminated", "resigned", "retired"])
        .values("position_id")
        .annotate(count=Count("id"))
    }
    for position in positions:
        position.employee_count = counts.get(position.id, 0)
    return render(
        request,
        "hr/positions.html",
        {
            "page_title": "Positions - SORA ERP",
            "positions": positions,
            "search_query": query,
            "status_filter": status_filter,
            "can_manage": access.is_admin or access.is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def position_create(request):
    _hr_admin_required(request)
    if request.method == "POST":
        form = PositionForm(request.POST)
        if form.is_valid():
            position = form.save(commit=False)
            position.created_at = timezone.now()
            position.save()
            messages.success(request, "Position created.")
            return redirect("/hr/positions")
    else:
        form = PositionForm()
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Position - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("hr")
def position_edit(request, id):
    _hr_admin_required(request)
    position = get_object_or_404(Position, pk=id)
    if request.method == "POST":
        form = PositionForm(request.POST, instance=position)
        if form.is_valid():
            position = form.save(commit=False)
            position.updated_at = timezone.now()
            position.save()
            messages.success(request, "Position updated.")
            return redirect("/hr/positions")
    else:
        form = PositionForm(instance=position)
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "Edit Position - SORA ERP", "form": form, "position": position, "is_edit": True},
    )


@login_required
@module_access_required("hr")
def position_view(request, id):
    _hr_staff(request)
    position = get_object_or_404(Position, pk=id)
    employees = list(
        Employee.objects.filter(position_id=position.id).exclude(
            employment_status__in=["terminated", "resigned", "retired"]
        )
    )
    return render(
        request,
        "hr/position_view.html",
        {"page_title": position.position_name, "position": position, "employees": employees},
    )


@login_required
@module_access_required("hr")
def position_delete(request, id):
    _hr_admin_required(request)
    position = get_object_or_404(Position, pk=id)
    if request.method == "POST":
        if Employee.objects.filter(position_id=position.id).exclude(
            employment_status__in=["terminated", "resigned", "retired"]
        ).exists():
            messages.error(request, "Reassign active employees before deleting this position.")
        else:
            position.delete()
            messages.success(request, "Position deleted.")
    return redirect("/hr/positions")


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def attendance_index(request):
    access = get_access(request.user)
    if access.is_employee and not access.is_hr_viewer and not access.is_hr_admin:
        return redirect("/hr/attendance/my-time")
    if access.is_hr_viewer and not access.is_hr_admin:
        pass
    today = timezone.localdate()
    selected_date = today
    raw_date = request.GET.get("date")
    if raw_date:
        try:
            selected_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            selected_date = today
    department_filter = request.GET.get("department") or ""
    employees = Employee.objects.exclude(
        employment_status__in=["terminated", "resigned", "retired"]
    ).filter(status="active")
    if department_filter:
        employees = employees.filter(department_id=department_filter)
    employees = list(employees.order_by("first_name", "last_name"))
    record_map = {
        record.employee_id: record
        for record in Attendance.objects.filter(date=selected_date)
    }
    departments = _departments_map([employee.department_id for employee in employees])
    rows = []
    present = late = absent = 0
    for employee in employees:
        record = record_map.get(employee.id)
        if record:
            if record.status == "present":
                present += 1
            elif record.status == "late":
                late += 1
            elif record.status == "absent":
                absent += 1
        rows.append(
            {
                "employee": employee,
                "department": departments.get(employee.department_id),
                "record": record,
            }
        )
    unrecorded = sum(1 for row in rows if row["record"] is None)
    return render(
        request,
        "hr/attendance.html",
        {
            "page_title": "Attendance - SORA ERP",
            "rows": rows,
            "selected_date": selected_date,
            "department_filter": department_filter,
            "departments": sorted(_departments_map().values(), key=lambda d: d.department_name),
            "stats": {
                "present": present,
                "late": late,
                "absent": absent,
                "unrecorded": unrecorded,
            },
        },
    )


@login_required
@module_access_required("hr")
def attendance_my_time(request):
    employee = _require_employee(request)
    if employee is None:
        return redirect("/hr/employee/dashboard")
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
        if not 1 <= month <= 12:
            raise ValueError
    except (ValueError, TypeError):
        year, month = today.year, today.month
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    records = list(
        Attendance.objects.filter(employee_id=employee.id, date__gte=start, date__lte=end).order_by("-date")
    )
    totals = {
        "days": len(records),
        "late": sum(1 for record in records if record.status == "late"),
        "absent": sum(1 for record in records if record.status == "absent"),
        "hours": sum((record.total_hours or Decimal("0")) for record in records),
        "overtime": sum((record.overtime_hours or Decimal("0")) for record in records),
    }
    today_record = Attendance.objects.filter(employee_id=employee.id, date=today).first()
    return render(
        request,
        "hr/attendance_my_time.html",
        {
            "page_title": "My Attendance - SORA ERP",
            "employee": employee,
            "records": records,
            "totals": totals,
            "month": month,
            "year": year,
            "today_record": today_record,
            "month_label": start.strftime("%B %Y"),
        },
    )


@login_required
@module_access_required("hr")
def attendance_summary(request):
    _hr_staff(request)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
        if not 1 <= month <= 12:
            raise ValueError
    except (ValueError, TypeError):
        year, month = today.year, today.month
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    department_filter = request.GET.get("department") or ""
    employees = Employee.objects.exclude(
        employment_status__in=["terminated", "resigned", "retired"]
    ).filter(status="active")
    if department_filter:
        employees = employees.filter(department_id=department_filter)
    employees = list(employees.order_by("first_name", "last_name"))
    employee_ids = [employee.id for employee in employees]
    aggregates = {
        row["employee_id"]: row
        for row in Attendance.objects.filter(
            employee_id__in=employee_ids, date__gte=start, date__lte=end
        )
        .values("employee_id")
        .annotate(
            days=Count("id"),
            hours=Sum("total_hours"),
            overtime=Sum("overtime_hours"),
            late=Count("id", filter=Q(status="late")),
            absent=Count("id", filter=Q(status="absent")),
        )
    }
    departments = _departments_map([employee.department_id for employee in employees])
    rows = []
    for employee in employees:
        aggregate = aggregates.get(employee.id, {})
        rows.append(
            {
                "employee": employee,
                "department": departments.get(employee.department_id),
                "days": aggregate.get("days") or 0,
                "hours": aggregate.get("hours") or Decimal("0"),
                "overtime": aggregate.get("overtime") or Decimal("0"),
                "late": aggregate.get("late") or 0,
                "absent": aggregate.get("absent") or 0,
            }
        )
    return render(
        request,
        "hr/attendance_summary.html",
        {
            "page_title": "Attendance Summary - SORA ERP",
            "rows": rows,
            "month": month,
            "year": year,
            "month_label": start.strftime("%B %Y"),
            "department_filter": department_filter,
            "departments": sorted(_departments_map().values(), key=lambda d: d.department_name),
        },
    )


@login_required
@module_access_required("hr")
def attendance_clock_in(request):
    if request.method != "POST":
        return redirect("/hr/attendance/my-time")
    employee, record = _attendance_for_user(request.user)
    if employee is None:
        messages.error(request, "Your user is not linked to an employee record.")
        return redirect("/hr/employee/dashboard")
    now = timezone.localtime()
    if record and record.clock_in:
        messages.warning(request, "You are already clocked in today.")
        return redirect(request.POST.get("next") or "/hr/attendance/my-time")
    if record is None:
        record = Attendance(employee_id=employee.id, date=timezone.localdate(), created_at=now)
    record.clock_in = now.time().replace(microsecond=0)
    record.check_in_method = "web"
    record.source = "web"
    record.status = "present"
    if record.clock_in > WORK_START:
        record.status = "late"
        start_dt = datetime.combine(record.date, WORK_START)
        record.late_minutes = max(0, int((now.replace(tzinfo=None) - start_dt).total_seconds() // 60))
    record.save()
    messages.success(request, f"Clocked in at {record.clock_in.strftime('%H:%M')}.")
    return redirect(request.POST.get("next") or "/hr/attendance/my-time")


@login_required
@module_access_required("hr")
def attendance_clock_out(request):
    if request.method != "POST":
        return redirect("/hr/attendance/my-time")
    employee, record = _attendance_for_user(request.user)
    if employee is None:
        messages.error(request, "Your user is not linked to an employee record.")
        return redirect("/hr/employee/dashboard")
    if record is None or not record.clock_in:
        messages.error(request, "You have not clocked in today.")
        return redirect("/hr/attendance/my-time")
    if record.clock_out:
        messages.warning(request, "You are already clocked out today.")
        return redirect(request.POST.get("next") or "/hr/attendance/my-time")
    now = timezone.localtime()
    record.clock_out = now.time().replace(microsecond=0)
    record.check_out_method = "web"
    start_dt = datetime.combine(record.date, record.clock_in)
    end_dt = datetime.combine(record.date, record.clock_out)
    if end_dt < start_dt:
        end_dt += timedelta(days=1)
    seconds = max(int((end_dt - start_dt).total_seconds()) - int(record.total_break or 0) * 60, 0)
    record.total_seconds = seconds
    record.total_hours = Decimal(str(round(seconds / 3600, 2)))
    record.overtime_hours = max(Decimal("0"), record.total_hours - STANDARD_HOURS)
    if record.clock_out < WORK_END:
        end_work = datetime.combine(record.date, WORK_END)
        record.early_departure_minutes = max(0, int((end_work - end_dt).total_seconds() // 60))
    record.save()
    messages.success(request, f"Clocked out at {record.clock_out.strftime('%H:%M')}.")
    return redirect(request.POST.get("next") or "/hr/attendance/my-time")


# ---------------------------------------------------------------------------
# Employee self-service dashboard
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def employee_dashboard(request):
    employee = _require_employee(request)
    if employee is None:
        return render(
            request,
            "hr/employee_dashboard.html",
            {"page_title": "My Dashboard - SORA ERP", "employee": None},
        )
    today = timezone.localdate()
    year = today.year
    _ensure_leave_balances(employee, year)
    balances = list(LeaveBalance.objects.filter(employee_id=employee.id, year=year))
    leave_types = _leave_types_map()
    for balance in balances:
        leave_type = leave_types.get(balance.leave_type_id)
        balance.type_name = getattr(leave_type, "leave_name", "Leave")
        balance.color = getattr(leave_type, "color", "#FFD700")
    pending = list(LeaveRequest.objects.filter(employee_id=employee.id, status="pending"))
    approved_days = (
        LeaveRequest.objects.filter(
            employee_id=employee.id,
            status="approved",
            start_date__year=year,
        ).aggregate(total=Sum("duration_days"))["total"]
        or Decimal("0")
    )
    recent = list(
        LeaveRequest.objects.filter(employee_id=employee.id).order_by("-created_at")[:6]
    )
    for leave in recent:
        leave.type_name = getattr(leave_types.get(leave.leave_type_id), "leave_name", "Leave")
    today_record = Attendance.objects.filter(employee_id=employee.id, date=today).first()
    goals = list(Goal.objects.filter(employee_id=employee.id).exclude(status="cancelled").order_by("target_date")[:5])
    trainings = list(EmployeeTraining.objects.filter(employee_id=employee.id).exclude(status="completed")[:5])
    programs = {
        program.id: program for program in TrainingProgram.objects.filter(
            id__in=[row.training_id for row in trainings]
        )
    }
    for row in trainings:
        row.program_name = getattr(programs.get(row.training_id), "program_name", "Training")
    return render(
        request,
        "hr/employee_dashboard.html",
        {
            "page_title": "My Dashboard - SORA ERP",
            "employee": employee,
            "balances": balances,
            "pending_count": len(pending),
            "approved_days": approved_days,
            "recent_leave": recent,
            "today_record": today_record,
            "goals": goals,
            "trainings": trainings,
            "year": year,
        },
    )


# ---------------------------------------------------------------------------
# Leave management
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def leave_index(request):
    access = get_access(request.user)
    today = timezone.localdate()
    if access.is_employee and not access.is_hr_viewer and not access.is_hr_admin:
        return redirect("/hr/leave/my-requests")
    pending = LeaveRequest.objects.filter(status="pending").count()
    approved_this_month = LeaveRequest.objects.filter(
        status="approved", start_date__year=today.year, start_date__month=today.month
    ).count()
    on_leave_today = (
        LeaveRequest.objects.filter(
            status="approved", start_date__lte=today, end_date__gte=today
        )
        .values("employee_id")
        .distinct()
        .count()
    )
    active_types = LeaveType.objects.filter(status="active").count()
    recent = list(LeaveRequest.objects.order_by("-created_at")[:10])
    _leave_row_employees(recent)
    leave_types = _leave_types_map()
    for leave in recent:
        leave.type_name = getattr(leave_types.get(leave.leave_type_id), "leave_name", "Leave")
    type_rows = []
    for leave_type in LeaveType.objects.filter(status="active").order_by("leave_name"):
        type_rows.append(
            {
                "type": leave_type,
                "requests": LeaveRequest.objects.filter(leave_type_id=leave_type.id).count(),
                "approved": LeaveRequest.objects.filter(
                    leave_type_id=leave_type.id, status="approved"
                ).count(),
            }
        )
    return render(
        request,
        "hr/leave.html",
        {
            "page_title": "Leave Management - SORA ERP",
            "stats": {
                "pending": pending,
                "approved_this_month": approved_this_month,
                "on_leave_today": on_leave_today,
                "types": active_types,
            },
            "recent_leave": recent,
            "type_rows": type_rows,
            "can_manage": access.is_hr_admin or access.is_admin,
        },
    )


@login_required
@module_access_required("hr")
def leave_request(request):
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    if employee is None and not (access.is_hr_admin or access.is_admin):
        messages.error(request, "Your user account is not linked to an employee record.")
        return redirect("/hr/employee/dashboard")
    if request.method == "POST":
        data = request.POST.copy()
        if not (access.is_hr_admin or access.is_admin):
            data["employee"] = employee.id
        form = LeaveRequestForm(data, request.FILES)
        if form.is_valid():
            leave = form.save(commit=False)
            start = form.cleaned_data["start_date"]
            end = form.cleaned_data["end_date"]
            leave_type = form.cleaned_data["leave_type"]
            days = _weekday_count(start, end)
            if days <= 0:
                form.add_error("start_date", "The selected range contains no working days.")
            elif _overlapping_leaves(leave.employee_id, start, end).exists():
                form.add_error("start_date", "This overlaps an existing pending or approved leave.")
            else:
                if (leave_type.requires_documentation or leave_type.requires_document) and not request.FILES.get(
                    "attachment"
                ):
                    form.add_error("attachment", "This leave type requires supporting documentation.")
                else:
                    balance = _get_balance(leave.employee_id, leave_type.id, start.year)
                    if balance is None:
                        _ensure_leave_balances(Employee.objects.get(pk=leave.employee_id), start.year)
                        balance = _get_balance(leave.employee_id, leave_type.id, start.year)
                    remaining = (balance.remaining_days if balance else Decimal("0")) or Decimal("0")
                    if balance and remaining < days:
                        form.add_error(
                            "end_date",
                            f"Insufficient balance: {remaining} day(s) remaining, {days} requested.",
                        )
                    else:
                        upload = request.FILES.get("attachment")
                        if upload:
                            leave.attachment = save_tenant_upload("leave", upload)
                        leave.duration_days = Decimal(days)
                        leave.status = "pending"
                        leave.created_at = timezone.now()
                        leave.save()
                        messages.success(request, "Leave request submitted for approval.")
                        return redirect("/hr/leave/my-requests")
    else:
        form = LeaveRequestForm(initial={"employee": employee, "start_date": timezone.localdate()})
    return render(
        request,
        "hr/form_page.html",
        {
            "page_title": "Request Leave - SORA ERP",
            "form": form,
            "can_select_employee": access.is_hr_admin or access.is_admin,
            "hide_employee": not (access.is_hr_admin or access.is_admin),
        },
    )


@login_required
@module_access_required("hr")
def leave_store(request):
    return leave_request(request)


@login_required
@module_access_required("hr")
def leave_my_requests(request):
    employee = _require_employee(request)
    if employee is None:
        return redirect("/hr/employee/dashboard")
    status_filter = request.GET.get("status") or ""
    queryset = LeaveRequest.objects.filter(employee_id=employee.id)
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    queryset = queryset.order_by("-created_at")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    leave_types = _leave_types_map()
    for leave in page_obj:
        leave.type_name = getattr(leave_types.get(leave.leave_type_id), "leave_name", "Leave")
    return render(
        request,
        "hr/leave_my_requests.html",
        {
            "page_title": "My Leave - SORA ERP",
            "page_obj": page_obj,
            "leaves": page_obj.object_list,
            "status_filter": status_filter,
            "status_choices": LeaveRequest._meta.get_field("status").choices,
            "employee": employee,
        },
    )


@login_required
@module_access_required("hr")
def leave_pending(request):
    access = get_access(request.user)
    if access.is_employee and not access.is_hr_viewer and not access.is_hr_admin:
        raise PermissionDenied("Approver access required.")
    queryset = LeaveRequest.objects.filter(status="pending").order_by("-created_at")
    if not (access.is_hr_admin or access.is_admin):
        employee = _employee_for_user(request.user)
        if employee is None:
            queryset = queryset.none()
        else:
            direct = Employee.objects.filter(reports_to=employee.id).values_list("id", flat=True)
            queryset = queryset.filter(employee_id__in=direct)
    leaves = list(queryset)
    _leave_row_employees(leaves)
    leave_types = _leave_types_map()
    for leave in leaves:
        leave.type_name = getattr(leave_types.get(leave.leave_type_id), "leave_name", "Leave")
    return render(
        request,
        "hr/leave_pending.html",
        {"page_title": "Pending Leave - SORA ERP", "leaves": leaves},
    )


def _leave_type_name(leave):
    leave_type = _leave_types_map().get(leave.leave_type_id)
    return getattr(leave_type, "leave_name", "Leave")


@login_required
@module_access_required("hr")
def leave_view(request, id):
    leave = get_object_or_404(LeaveRequest, pk=id)
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    is_owner = employee is not None and leave.employee_id == employee.id
    if not (is_owner or _can_approve_leave(request.user, leave) or access.is_hr_viewer):
        raise PermissionDenied("You cannot view this leave request.")
    _leave_row_employees([leave])
    leave_type = _leave_types_map().get(leave.leave_type_id)
    leave.type_name = getattr(leave_type, "leave_name", "Leave")
    approver = User.objects.filter(pk=leave.approved_by).first()
    balance = _get_balance(leave.employee_id, leave.leave_type_id, leave.start_date.year)
    can_approve = leave.status == "pending" and _can_approve_leave(request.user, leave)
    can_cancel = leave.status == "pending" and is_owner
    return render(
        request,
        "hr/leave_view.html",
        {
            "page_title": "Leave Request - SORA ERP",
            "leave": leave,
            "leave_type": leave_type,
            "approver": approver,
            "balance": balance,
            "can_approve": can_approve,
            "can_cancel": can_cancel,
        },
    )


@login_required
@module_access_required("hr")
@transaction.atomic
def leave_approve(request, id):
    if request.method != "POST":
        return redirect(f"/hr/leave/view/{id}")
    leave = get_object_or_404(LeaveRequest, pk=id)
    if not _can_approve_leave(request.user, leave):
        raise PermissionDenied("You cannot approve this request.")
    if leave.status != "pending":
        messages.warning(request, "This request has already been processed.")
        return redirect(f"/hr/leave/view/{leave.id}")
    days = _weekday_count(leave.start_date, leave.end_date)
    balance = _get_balance(leave.employee_id, leave.leave_type_id, leave.start_date.year)
    if balance is None:
        employee = Employee.objects.filter(pk=leave.employee_id).first()
        if employee:
            _ensure_leave_balances(employee, leave.start_date.year)
        balance = _get_balance(leave.employee_id, leave.leave_type_id, leave.start_date.year)
    remaining = (balance.remaining_days if balance else Decimal("0")) or Decimal("0")
    if balance is None or remaining < days:
        messages.error(request, f"Cannot approve: only {remaining} day(s) remain but {days} are requested.")
        return redirect(f"/hr/leave/view/{leave.id}")
    if _overlapping_leaves(
        leave.employee_id, leave.start_date, leave.end_date, exclude_id=leave.id
    ).filter(status="approved").exists():
        messages.error(request, "Another approved leave overlaps this period.")
        return redirect(f"/hr/leave/view/{leave.id}")

    balance.used_days = (balance.used_days or Decimal("0")) + Decimal(days)
    balance.remaining_days = remaining - Decimal(days)
    if balance.pending_days and balance.pending_days >= days:
        balance.pending_days = balance.pending_days - Decimal(days)
    balance.updated_at = timezone.now()
    balance.save(update_fields=["used_days", "remaining_days", "pending_days", "updated_at"])
    leave.status = "approved"
    leave.duration_days = Decimal(days)
    leave.approved_by = request.user.id
    leave.approved_at = timezone.now()
    leave.save(update_fields=["status", "duration_days", "approved_by", "approved_at"])
    LeaveTransaction.objects.create(
        employee_id=leave.employee_id,
        transaction_type="used",
        days=Decimal(days),
        reference=f"LV-{leave.id}",
        description=f"{_leave_type_name(leave)} approved by {request.user.get_full_name() or request.user.email}",
        created_at=timezone.now(),
    )
    messages.success(request, f"Leave approved for {days} working day(s).")
    return redirect(f"/hr/leave/view/{leave.id}")


@login_required
@module_access_required("hr")
@transaction.atomic
def leave_reject(request, id):
    if request.method != "POST":
        return redirect(f"/hr/leave/view/{id}")
    leave = get_object_or_404(LeaveRequest, pk=id)
    if not _can_approve_leave(request.user, leave):
        raise PermissionDenied("You cannot reject this request.")
    if leave.status != "pending":
        messages.warning(request, "This request has already been processed.")
        return redirect(f"/hr/leave/view/{leave.id}")
    reason = (request.POST.get("rejection_reason") or "").strip()
    if not reason:
        messages.error(request, "A rejection reason is required.")
        return redirect(f"/hr/leave/view/{leave.id}")
    leave.status = "rejected"
    leave.rejection_reason = reason
    leave.approved_by = request.user.id
    leave.approved_at = timezone.now()
    leave.save(update_fields=["status", "rejection_reason", "approved_by", "approved_at"])
    messages.success(request, "Leave request rejected.")
    return redirect(f"/hr/leave/view/{leave.id}")


@login_required
@module_access_required("hr")
def leave_cancel(request, id):
    if request.method != "POST":
        return redirect(f"/hr/leave/view/{id}")
    leave = get_object_or_404(LeaveRequest, pk=id)
    employee = _employee_for_user(request.user)
    if employee is None or leave.employee_id != employee.id:
        raise PermissionDenied("You can only cancel your own leave request.")
    if leave.status != "pending":
        messages.error(request, "Only pending requests can be cancelled.")
        return redirect(f"/hr/leave/view/{leave.id}")
    leave.status = "cancelled"
    leave.cancelled_by = request.user.id
    leave.cancelled_at = timezone.now()
    leave.save(update_fields=["status", "cancelled_by", "cancelled_at"])
    messages.success(request, "Leave request cancelled.")
    return redirect("/hr/leave/my-requests")


@login_required
@module_access_required("hr")
def leave_calendar(request):
    access = get_access(request.user)
    employee_scope = None
    if access.is_employee and not access.is_hr_viewer and not access.is_hr_admin:
        employee_scope = _employee_for_user(request.user)
        if employee_scope is None:
            return redirect("/hr/employee/dashboard")
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
        if not 1 <= month <= 12:
            raise ValueError
    except (ValueError, TypeError):
        year, month = today.year, today.month
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    leaves = list(
        LeaveRequest.objects.filter(
            status__in=["approved", "pending"], start_date__lte=last, end_date__gte=first
        )
    )
    if employee_scope is not None:
        leaves = [leave for leave in leaves if leave.employee_id == employee_scope.id]
    _leave_row_employees(leaves)
    leave_types = _leave_types_map()
    for leave in leaves:
        leave.type_name = getattr(leave_types.get(leave.leave_type_id), "leave_name", "Leave")
        leave.color = getattr(leave_types.get(leave.leave_type_id), "color", "#FFD700")
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        days = []
        for day in week:
            days.append(
                {
                    "date": day,
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "leaves": [leave for leave in leaves if leave.start_date <= day <= leave.end_date]
                    if day.month == month
                    else [],
                }
            )
        weeks.append(days)
    return render(
        request,
        "hr/leave_calendar.html",
        {
            "page_title": "Leave Calendar - SORA ERP",
            "weeks": weeks,
            "month": month,
            "year": year,
            "month_label": first.strftime("%B %Y"),
            "prev_month": first - timedelta(days=1),
            "next_month": last + timedelta(days=1),
        },
    )


@login_required
@module_access_required("hr")
def leave_balance(request):
    access = get_access(request.user)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (ValueError, TypeError):
        year = today.year
    employee_id = request.GET.get("employee") or ""
    if access.is_employee and not access.is_hr_viewer and not access.is_hr_admin:
        employee = _employee_for_user(request.user)
    elif employee_id:
        employee = Employee.objects.filter(pk=employee_id).first()
    else:
        employee = _employee_for_user(request.user) or Employee.objects.filter(status="active").first()
    if employee is None:
        messages.error(request, "No employee record available.")
        return redirect("/hr/employee/dashboard")
    _ensure_leave_balances(employee, year)
    balances = list(LeaveBalance.objects.filter(employee_id=employee.id, year=year))
    leave_types = _leave_types_map()
    for balance in balances:
        leave_type = leave_types.get(balance.leave_type_id)
        balance.type_name = getattr(leave_type, "leave_name", "Leave")
        balance.color = getattr(leave_type, "color", "#FFD700")
        balance.used = balance.used_days or Decimal("0")
    can_select = access.is_hr_admin or access.is_admin or access.is_hr_viewer
    return render(
        request,
        "hr/leave_balance.html",
        {
            "page_title": "Leave Balance - SORA ERP",
            "employee": employee,
            "balances": balances,
            "year": year,
            "years": range(today.year - 2, today.year + 2),
            "can_select_employee": can_select,
            "employees": Employee.objects.exclude(status="inactive").order_by("first_name")
            if can_select
            else [],
        },
    )


@login_required
@module_access_required("hr")
def leave_types(request):
    _hr_admin_required(request)
    types = list(LeaveType.objects.order_by("leave_name"))
    counts = {
        row["leave_type_id"]: row["count"]
        for row in LeaveBalance.objects.values("leave_type_id").annotate(count=Count("id"))
    }
    for leave_type in types:
        leave_type.balance_count = counts.get(leave_type.id, 0)
    return render(
        request,
        "hr/leave_types.html",
        {"page_title": "Leave Types - SORA ERP", "leave_types": types},
    )


@login_required
@module_access_required("hr")
def leave_type_create(request):
    _hr_admin_required(request)
    if request.method == "POST":
        form = LeaveTypeForm(request.POST)
        if form.is_valid():
            leave_type = form.save(commit=False)
            leave_type.created_at = timezone.now()
            leave_type.save()
            for employee in Employee.objects.filter(status="active"):
                _ensure_leave_balances(employee)
            messages.success(request, "Leave type created and balances initialised.")
            return redirect("/hr/leave/types")
    else:
        form = LeaveTypeForm()
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Leave Type - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("hr")
def leave_type_edit(request, id):
    _hr_admin_required(request)
    leave_type = get_object_or_404(LeaveType, pk=id)
    if request.method == "POST":
        form = LeaveTypeForm(request.POST, instance=leave_type)
        if form.is_valid():
            leave_type = form.save(commit=False)
            leave_type.updated_at = timezone.now()
            leave_type.save()
            messages.success(request, "Leave type updated.")
            return redirect("/hr/leave/types")
    else:
        form = LeaveTypeForm(instance=leave_type)
    return render(
        request,
        "hr/form_page.html",
        {
            "page_title": "Edit Leave Type - SORA ERP",
            "form": form,
            "leave_type": leave_type,
            "is_edit": True,
        },
    )


# ---------------------------------------------------------------------------
# Overtime
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def overtime_index(request):
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    if request.method == "POST":
        data = request.POST.copy()
        if not (access.is_hr_admin or access.is_admin):
            if employee is None:
                messages.error(request, "Your user account is not linked to an employee record.")
                return redirect("/hr/employee/dashboard")
            data["employee"] = employee.id
        form = OvertimeRequestForm(data)
        if form.is_valid():
            overtime = form.save(commit=False)
            if overtime.overtime_date > timezone.localdate():
                messages.error(request, "Overtime cannot be logged for a future date.")
            elif OvertimeRequest.objects.filter(
                employee_id=overtime.employee_id,
                overtime_date=overtime.overtime_date,
                status="pending",
            ).exists():
                messages.error(request, "A pending overtime request already exists for that date.")
            else:
                overtime.status = "pending"
                overtime.created_at = timezone.now()
                overtime.save()
                messages.success(request, "Overtime request submitted.")
                return redirect("/hr/leave/overtime")
        else:
            messages.error(request, "Please correct the overtime request details.")
    else:
        form = OvertimeRequestForm(initial={"employee": employee, "overtime_date": timezone.localdate()})
    if access.is_hr_admin or access.is_admin:
        queryset = OvertimeRequest.objects.all()
    elif employee is not None:
        queryset = OvertimeRequest.objects.filter(employee_id=employee.id)
    else:
        queryset = OvertimeRequest.objects.none()
    queryset = queryset.order_by("-overtime_date")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    _leave_row_employees(list(page_obj))
    return render(
        request,
        "hr/overtime.html",
        {
            "page_title": "Overtime - SORA ERP",
            "page_obj": page_obj,
            "requests": page_obj.object_list,
            "form": form,
            "can_select_employee": access.is_hr_admin or access.is_admin,
            "can_manage": access.is_hr_admin or access.is_admin,
        },
    )


@login_required
@module_access_required("hr")
def overtime_store(request):
    return overtime_index(request)


@login_required
@module_access_required("hr")
def pending_overtime(request):
    access = get_access(request.user)
    if access.is_employee and not access.is_hr_viewer and not access.is_hr_admin:
        raise PermissionDenied("Approver access required.")
    queryset = OvertimeRequest.objects.filter(status="pending").order_by("-overtime_date")
    if not (access.is_hr_admin or access.is_admin):
        employee = _employee_for_user(request.user)
        if employee is None:
            queryset = queryset.none()
        else:
            direct = Employee.objects.filter(reports_to=employee.id).values_list("id", flat=True)
            queryset = queryset.filter(employee_id__in=direct)
    requests = list(queryset)
    _leave_row_employees(requests)
    return render(
        request,
        "hr/overtime_pending.html",
        {
            "page_title": "Pending Overtime - SORA ERP",
            "requests": requests,
            "leave_types": LeaveType.objects.filter(status="active").order_by("leave_name"),
        },
    )


@login_required
@module_access_required("hr")
@transaction.atomic
def overtime_approve(request, id):
    if request.method != "POST":
        return redirect("/hr/leave/pending-overtime")
    overtime = get_object_or_404(OvertimeRequest, pk=id)
    if not _can_approve_overtime(request.user, overtime):
        raise PermissionDenied("You cannot approve this overtime request.")
    if overtime.status != "pending":
        messages.warning(request, "This overtime request has already been processed.")
        return redirect("/hr/leave/pending-overtime")
    overtime.status = "approved"
    overtime.approved_by = request.user.id
    overtime.approved_at = timezone.now()
    overtime.updated_at = timezone.now()
    leave_type_id = request.POST.get("leave_type")
    if leave_type_id:
        leave_type = LeaveType.objects.filter(pk=leave_type_id, status="active").first()
        if leave_type is None:
            messages.error(request, "Select a valid leave type for conversion.")
            return redirect("/hr/leave/pending-overtime")
        days = (overtime.hours / (overtime.conversion_rate or Decimal("8"))).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        balance = _get_balance(overtime.employee_id, leave_type.id, overtime.overtime_date.year)
        if balance is None:
            employee = Employee.objects.filter(pk=overtime.employee_id).first()
            if employee:
                _ensure_leave_balances(employee, overtime.overtime_date.year)
            balance = _get_balance(overtime.employee_id, leave_type.id, overtime.overtime_date.year)
        if balance is None:
            messages.error(request, "Could not initialise a leave balance for the conversion.")
            return redirect("/hr/leave/pending-overtime")
        balance.total_earned = (balance.total_earned or Decimal("0")) + days
        balance.remaining_days = (balance.remaining_days or Decimal("0")) + days
        balance.updated_at = timezone.now()
        balance.save(update_fields=["total_earned", "remaining_days", "updated_at"])
        overtime.converted_to_leave = 1
        LeaveTransaction.objects.create(
            employee_id=overtime.employee_id,
            transaction_type="earned",
            days=days,
            reference=f"OT-{overtime.id}",
            description=f"Overtime conversion to {leave_type.leave_name}",
            created_at=timezone.now(),
        )
        messages.success(request, f"Overtime approved and {days} day(s) credited to {leave_type.leave_name}.")
    else:
        messages.success(request, "Overtime approved.")
    overtime.save()
    return redirect("/hr/leave/pending-overtime")


@login_required
@module_access_required("hr")
def overtime_reject(request, id):
    if request.method != "POST":
        return redirect("/hr/leave/pending-overtime")
    overtime = get_object_or_404(OvertimeRequest, pk=id)
    if not _can_approve_overtime(request.user, overtime):
        raise PermissionDenied("You cannot reject this overtime request.")
    if overtime.status != "pending":
        messages.warning(request, "This overtime request has already been processed.")
        return redirect("/hr/leave/pending-overtime")
    overtime.status = "rejected"
    overtime.approved_by = request.user.id
    overtime.approved_at = timezone.now()
    overtime.updated_at = timezone.now()
    overtime.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    messages.success(request, "Overtime request rejected.")
    return redirect("/hr/leave/pending-overtime")


# ---------------------------------------------------------------------------
# Approvers
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def approvers(request):
    _hr_admin_required(request)
    rows = list(Approver.objects.order_by("approval_level", "priority"))
    employees = _employees_map([row.employee_id for row in rows])
    departments = _departments_map([row.department_id for row in rows])
    for row in rows:
        employee = employees.get(row.employee_id)
        row.employee_name = (
            f"{employee.first_name} {employee.last_name}" if employee else f"Employee #{row.employee_id}"
        )
        row.department_name = getattr(departments.get(row.department_id), "department_name", "All departments")
    return render(
        request,
        "hr/approvers.html",
        {"page_title": "Leave Approvers - SORA ERP", "approvers": rows},
    )


@login_required
@module_access_required("hr")
def approver_create(request):
    _hr_admin_required(request)
    if request.method == "POST":
        form = ApproverForm(request.POST)
        if form.is_valid():
            approver = form.save(commit=False)
            approver.created_at = timezone.now()
            approver.save()
            messages.success(request, "Approver added.")
            return redirect("/hr/leave/approvers")
    else:
        form = ApproverForm()
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Approver - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("hr")
def approver_edit(request, id):
    _hr_admin_required(request)
    approver = get_object_or_404(Approver, pk=id)
    if request.method == "POST":
        form = ApproverForm(request.POST, instance=approver)
        if form.is_valid():
            approver = form.save(commit=False)
            approver.updated_at = timezone.now()
            approver.save()
            messages.success(request, "Approver updated.")
            return redirect("/hr/leave/approvers")
    else:
        form = ApproverForm(instance=approver)
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "Edit Approver - SORA ERP", "form": form, "approver": approver, "is_edit": True},
    )


@login_required
@module_access_required("hr")
def approver_delete(request, id):
    _hr_admin_required(request)
    approver = get_object_or_404(Approver, pk=id)
    if request.method == "POST":
        approver.delete()
        messages.success(request, "Approver removed.")
    return redirect("/hr/leave/approvers")


# ---------------------------------------------------------------------------
# Performance management
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def performance_index(request):
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    employee_only = access.is_employee and not access.is_hr_viewer and not access.is_hr_admin
    queryset = PerformanceReview.objects.all()
    if employee_only:
        if employee is None:
            return redirect("/hr/employee/dashboard")
        queryset = queryset.filter(employee_id=employee.id)
    status_filter = request.GET.get("status") or ""
    query = (request.GET.get("q") or "").strip()
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if query and not employee_only:
        matches = Employee.objects.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query)
        ).values("id")
        queryset = queryset.filter(Q(employee_id__in=matches))
    queryset = queryset.order_by("-review_period_end")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    reviews = list(page_obj)
    employees = _employees_map(
        [review.employee_id for review in reviews] + [review.reviewer_id for review in reviews]
    )
    for review in reviews:
        employee_row = employees.get(review.employee_id)
        reviewer = employees.get(review.reviewer_id)
        review.employee_name = (
            f"{employee_row.first_name} {employee_row.last_name}" if employee_row else "—"
        )
        review.reviewer_name = (
            f"{reviewer.first_name} {reviewer.last_name}" if reviewer else "—"
        )
    stats = {
        "reviews": queryset.count(),
        "average": queryset.aggregate(value=Avg("overall_rating"))["value"] or 0,
        "pending": queryset.filter(status__in=["draft", "submitted"]).count(),
        "goals_completed": Goal.objects.filter(status="completed").count(),
    }
    return render(
        request,
        "hr/performance.html",
        {
            "page_title": "Performance - SORA ERP",
            "page_obj": page_obj,
            "reviews": reviews,
            "stats": stats,
            "status_filter": status_filter,
            "status_choices": PerformanceReview._meta.get_field("status").choices,
            "search_query": query,
            "can_manage": access.is_hr_admin or access.is_admin,
            "employee_only": employee_only,
        },
    )


@login_required
@module_access_required("hr")
def performance_create(request):
    _hr_admin_required(request)
    employee = _employee_for_user(request.user)
    if request.method == "POST":
        form = PerformanceReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.status = "draft"
            review.created_at = timezone.now()
            review.save()
            messages.success(request, "Performance review created as a draft.")
            return redirect(f"/hr/performance/view/{review.id}")
    else:
        form = PerformanceReviewForm(initial={"reviewer": employee, "review_type": "annual"})
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Performance Review - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("hr")
def performance_store(request):
    return performance_create(request)


@login_required
@module_access_required("hr")
def performance_view(request, id):
    review = get_object_or_404(PerformanceReview, pk=id)
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    is_employee = employee is not None and review.employee_id == employee.id
    if not (is_employee or access.is_hr_viewer or access.is_hr_admin):
        raise PermissionDenied("You cannot view this review.")
    if request.method == "POST":
        action = request.POST.get("action")
        is_reviewer = employee is not None and review.reviewer_id == employee.id
        if action == "submit" and review.status == "draft" and (is_reviewer or access.is_hr_admin):
            review.status = "submitted"
            review.submitted_at = timezone.now()
            review.save(update_fields=["status", "submitted_at"])
            messages.success(request, "Review submitted.")
        elif action == "review" and review.status == "submitted" and access.is_hr_admin:
            review.status = "reviewed"
            review.reviewed_at = timezone.now()
            review.save(update_fields=["status", "reviewed_at"])
            messages.success(request, "Review marked reviewed.")
        elif action == "approve" and review.status in ["submitted", "reviewed"] and access.is_hr_admin:
            review.status = "approved"
            review.approved_at = timezone.now()
            review.save(update_fields=["status", "approved_at"])
            messages.success(request, "Review approved.")
        elif action == "complete" and review.status == "approved" and access.is_hr_admin:
            review.status = "completed"
            review.save(update_fields=["status"])
            messages.success(request, "Review completed.")
        else:
            messages.error(request, "That action is not available for this review.")
        return redirect(f"/hr/performance/view/{review.id}")
    people = _employees_map([review.employee_id, review.reviewer_id])
    employee_row = people.get(review.employee_id)
    reviewer = people.get(review.reviewer_id)
    can_submit = (
        review.status == "draft"
        and ((employee and review.reviewer_id == employee.id) or access.is_hr_admin)
    )
    return render(
        request,
        "hr/performance_view.html",
        {
            "page_title": "Performance Review - SORA ERP",
            "review": review,
            "employee_row": employee_row,
            "reviewer": reviewer,
            "can_submit": can_submit,
            "can_manage": access.is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def goals(request):
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    employee_only = access.is_employee and not access.is_hr_viewer and not access.is_hr_admin
    if request.method == "POST":
        goal = get_object_or_404(Goal, pk=request.POST.get("goal_id"))
        if employee_only and (employee is None or goal.employee_id != employee.id):
            raise PermissionDenied("You cannot update this goal.")
        try:
            progress = Decimal(request.POST.get("progress") or "0")
        except Exception:
            progress = None
        if progress is None or not (Decimal("0") <= progress <= Decimal("100")):
            messages.error(request, "Progress must be between 0 and 100.")
            return redirect("/hr/performance/goals")
        goal.progress = progress
        if progress >= 100:
            goal.status = "completed"
            goal.completed_at = timezone.now()
        elif progress > 0:
            goal.status = "in_progress"
            goal.completed_at = None
        else:
            goal.status = "not_started"
            goal.completed_at = None
        goal.save(update_fields=["progress", "status", "completed_at"])
        messages.success(request, "Goal progress updated.")
        return redirect("/hr/performance/goals")
    queryset = Goal.objects.all()
    if employee_only:
        if employee is None:
            return redirect("/hr/employee/dashboard")
        queryset = queryset.filter(employee_id=employee.id)
    status_filter = request.GET.get("status") or ""
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    queryset = queryset.order_by("target_date")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    goal_rows = list(page_obj)
    employees = _employees_map([goal.employee_id for goal in goal_rows])
    for goal in goal_rows:
        row = employees.get(goal.employee_id)
        goal.employee_name = f"{row.first_name} {row.last_name}" if row else "—"
    return render(
        request,
        "hr/goals.html",
        {
            "page_title": "Goals & KPIs - SORA ERP",
            "page_obj": page_obj,
            "goals": goal_rows,
            "status_filter": status_filter,
            "status_choices": Goal._meta.get_field("status").choices,
            "can_manage": access.is_hr_admin or access.is_admin,
        },
    )


@login_required
@module_access_required("hr")
def goal_create(request):
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    employee_only = access.is_employee and not access.is_hr_viewer and not access.is_hr_admin
    if not (access.is_hr_admin or employee_only):
        raise PermissionDenied("Goal creation is restricted.")
    if request.method == "POST":
        data = request.POST.copy()
        if employee_only:
            if employee is None:
                return redirect("/hr/employee/dashboard")
            data["employee"] = employee.id
        form = GoalForm(data)
        if form.is_valid():
            goal = form.save(commit=False)
            progress = goal.progress or Decimal("0")
            goal.status = (
                "completed" if progress >= 100 else "in_progress" if progress > 0 else "not_started"
            )
            goal.completed_at = timezone.now() if goal.status == "completed" else None
            goal.created_by = request.user.id
            goal.created_at = timezone.now()
            goal.save()
            messages.success(request, "Goal created.")
            return redirect("/hr/performance/goals")
    else:
        form = GoalForm(initial={"employee": employee, "start_date": timezone.localdate()})
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Goal - SORA ERP", "form": form},
    )


@login_required
@module_access_required("hr")
def goal_store(request):
    return goal_create(request)


@login_required
@module_access_required("hr")
def feedback(request):
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    employee_only = access.is_employee and not access.is_hr_viewer and not access.is_hr_admin
    if request.method == "POST":
        if employee is None:
            messages.error(request, "Your user account is not linked to an employee record.")
            return redirect("/hr/performance/feedback")
        form = FeedbackForm(request.POST)
        if form.is_valid():
            recipient = form.cleaned_data["to_employee"]
            if recipient.id == employee.id:
                messages.error(request, "You cannot give feedback to yourself.")
            else:
                feedback_row = EmployeeFeedback.objects.create(
                    from_employee_id=employee.id,
                    to_employee_id=recipient.id,
                    feedback_type=form.cleaned_data["feedback_type"],
                    message=form.cleaned_data["message"],
                    is_public=1 if form.cleaned_data.get("is_public") else 0,
                    created_at=timezone.now(),
                )
                messages.success(request, "Feedback shared.")
                return redirect("/hr/performance/feedback")
        else:
            messages.error(request, "Please correct the feedback form.")
    else:
        form = FeedbackForm()
    queryset = EmployeeFeedback.objects.all()
    if employee_only:
        if employee is None:
            return redirect("/hr/employee/dashboard")
        queryset = queryset.filter(Q(to_employee_id=employee.id) | Q(from_employee_id=employee.id))
    elif not (access.is_hr_admin or access.is_admin):
        queryset = queryset.filter(is_public=1)
    queryset = queryset.order_by("-created_at")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    rows = list(page_obj)
    people = _employees_map(
        [row.from_employee_id for row in rows] + [row.to_employee_id for row in rows]
    )
    for row in rows:
        sender = people.get(row.from_employee_id)
        recipient = people.get(row.to_employee_id)
        row.sender_name = (
            "Anonymous"
            if row.feedback_type == "anonymous" and not (access.is_admin or access.is_hr_admin)
            else (f"{sender.first_name} {sender.last_name}" if sender else "—")
        )
        row.recipient_name = f"{recipient.first_name} {recipient.last_name}" if recipient else "—"
    return render(
        request,
        "hr/feedback.html",
        {
            "page_title": "Feedback - SORA ERP",
            "page_obj": page_obj,
            "feedback_rows": rows,
            "form": form,
            "employee": employee,
            "can_give": employee is not None,
        },
    )


@login_required
@module_access_required("hr")
def feedback_store(request):
    return feedback(request)


# ---------------------------------------------------------------------------
# Recruitment
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def recruitment_jobs(request):
    access = _hr_staff(request)
    query = (request.GET.get("q") or "").strip()
    status_filter = request.GET.get("status") or ""
    queryset = JobPosting.objects.all()
    if query:
        queryset = queryset.filter(Q(job_title__icontains=query) | Q(location__icontains=query))
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    queryset = queryset.order_by("-posting_date")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    postings = list(page_obj)
    departments = _departments_map([posting.department_id for posting in postings])
    candidate_counts = {
        row["job_posting_id"]: row["count"]
        for row in Candidate.objects.filter(job_posting_id__in=[posting.id for posting in postings])
        .values("job_posting_id")
        .annotate(count=Count("id"))
    }
    today = timezone.localdate()
    for posting in postings:
        posting.department_name = getattr(
            departments.get(posting.department_id), "department_name", "—"
        )
        posting.candidate_count = candidate_counts.get(posting.id, 0)
        posting.is_closing_soon = bool(
            posting.closing_date
            and posting.status == "published"
            and today <= posting.closing_date <= today + timedelta(days=14)
        )
    stats = {
        "open": JobPosting.objects.filter(status="published").count(),
        "applications": Candidate.objects.count(),
        "recent": JobPosting.objects.filter(
            posting_date__gte=today - timedelta(days=30)
        ).count(),
        "closing": JobPosting.objects.filter(
            status="published",
            closing_date__gte=today,
            closing_date__lte=today + timedelta(days=14),
        ).count(),
    }
    return render(
        request,
        "hr/recruitment_jobs.html",
        {
            "page_title": "Job Openings - SORA ERP",
            "page_obj": page_obj,
            "postings": postings,
            "stats": stats,
            "search_query": query,
            "status_filter": status_filter,
            "status_choices": JobPosting._meta.get_field("status").choices,
            "can_manage": access.is_hr_admin or access.is_admin,
        },
    )


@login_required
@module_access_required("hr")
def recruitment_job_create(request):
    access = _hr_admin_required(request)
    if request.method == "POST":
        form = JobPostingForm(request.POST)
        if form.is_valid():
            posting = form.save(commit=False)
            posting.created_by = request.user.id
            posting.created_at = timezone.now()
            posting.save()
            messages.success(request, "Job opening created.")
            return redirect(f"/hr/recruitment/job/{posting.id}")
    else:
        form = JobPostingForm(initial={"posting_date": timezone.localdate(), "status": "draft"})
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Job Opening - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("hr")
def recruitment_job_store(request):
    return recruitment_job_create(request)


@login_required
@module_access_required("hr")
def recruitment_job_view(request, id):
    _hr_staff(request)
    posting = get_object_or_404(JobPosting, pk=id)
    department = _departments_map([posting.department_id]).get(posting.department_id)
    position = _positions_map([posting.position_id]).get(posting.position_id)
    candidates = list(Candidate.objects.filter(job_posting_id=posting.id).order_by("-created_at"))
    interviews = Interview.objects.filter(job_posting_id=posting.id).count()
    return render(
        request,
        "hr/job_view.html",
        {
            "page_title": posting.job_title,
            "posting": posting,
            "department": department,
            "position": position,
            "candidates": candidates,
            "interview_count": interviews,
            "can_manage": get_access(request.user).is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def recruitment_job_edit(request, id):
    _hr_admin_required(request)
    posting = get_object_or_404(JobPosting, pk=id)
    if request.method == "POST":
        form = JobPostingForm(request.POST, instance=posting)
        if form.is_valid():
            posting = form.save()
            messages.success(request, "Job opening updated.")
            return redirect(f"/hr/recruitment/job/{posting.id}")
    else:
        form = JobPostingForm(instance=posting)
    return render(
        request,
        "hr/form_page.html",
        {
            "page_title": "Edit Job Opening - SORA ERP",
            "form": form,
            "posting": posting,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("hr")
def recruitment_job_update(request, id):
    return recruitment_job_edit(request, id)


@login_required
@module_access_required("hr")
def recruitment_job_delete(request, id):
    _hr_admin_required(request)
    posting = get_object_or_404(JobPosting, pk=id)
    if request.method == "POST":
        if Candidate.objects.filter(job_posting_id=posting.id).exists():
            messages.error(request, "This opening has candidates and cannot be deleted. Close it instead.")
        else:
            posting.delete()
            messages.success(request, "Job opening deleted.")
    return redirect("/hr/recruitment/jobs")


@login_required
@module_access_required("hr")
def recruitment_candidates(request):
    _hr_staff(request)
    query = (request.GET.get("q") or "").strip()
    status_filter = request.GET.get("status") or ""
    job_filter = request.GET.get("job") or ""
    queryset = Candidate.objects.all()
    if query:
        queryset = queryset.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        )
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if job_filter:
        queryset = queryset.filter(job_posting_id=job_filter)
    queryset = queryset.order_by("-created_at")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    candidates = list(page_obj)
    jobs = {posting.id: posting for posting in JobPosting.objects.all()}
    for candidate in candidates:
        posting = jobs.get(candidate.job_posting_id)
        candidate.job_title = posting.job_title if posting else "—"
    funnel = {
        status: Candidate.objects.filter(status=status).count()
        for status, _label in Candidate._meta.get_field("status").choices
    }
    return render(
        request,
        "hr/candidates.html",
        {
            "page_title": "Candidates - SORA ERP",
            "page_obj": page_obj,
            "candidates": candidates,
            "funnel": funnel,
            "search_query": query,
            "status_filter": status_filter,
            "job_filter": job_filter,
            "status_choices": Candidate._meta.get_field("status").choices,
            "jobs": JobPosting.objects.order_by("-posting_date"),
            "can_manage": get_access(request.user).is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def recruitment_candidate_create(request):
    _hr_admin_required(request)
    initial = {}
    if request.GET.get("job"):
        initial["job_posting"] = request.GET.get("job")
    if request.method == "POST":
        form = CandidateForm(request.POST, request.FILES)
        if form.is_valid():
            candidate = form.save(commit=False)
            upload = request.FILES.get("resume")
            if upload:
                candidate.resume_path = save_tenant_upload("resumes", upload)
            candidate.status = "new"
            candidate.created_at = timezone.now()
            candidate.save()
            messages.success(request, "Candidate added.")
            return redirect(f"/hr/recruitment/candidate/{candidate.id}")
    else:
        form = CandidateForm(initial=initial)
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Candidate - SORA ERP", "form": form},
    )


@login_required
@module_access_required("hr")
def recruitment_candidate_store(request):
    return recruitment_candidate_create(request)


@login_required
@module_access_required("hr")
def recruitment_candidate_view(request, id):
    access = _hr_staff(request)
    candidate = get_object_or_404(Candidate, pk=id)
    posting = JobPosting.objects.filter(pk=candidate.job_posting_id).first()
    interviews = list(
        Interview.objects.filter(candidate_id=candidate.id).order_by("-scheduled_date")
    )
    interviewer_map = _employees_map([interview.interviewer_id for interview in interviews])
    for interview in interviews:
        interviewer = interviewer_map.get(interview.interviewer_id)
        interview.interviewer_name = (
            f"{interviewer.first_name} {interviewer.last_name}" if interviewer else "—"
        )
    if request.method == "POST":
        _hr_admin_required(request)
        new_status = request.POST.get("status")
        allowed = CANDIDATE_TRANSITIONS.get(candidate.status or "new", set())
        if new_status == candidate.status:
            messages.info(request, "The candidate is already in that stage.")
        elif new_status in allowed:
            candidate.status = new_status
            candidate.save(update_fields=["status"])
            messages.success(request, f"Candidate moved to {candidate.get_status_display()}.")
        else:
            messages.error(request, "That status transition is not allowed.")
        return redirect(f"/hr/recruitment/candidate/{candidate.id}")
    transitions = [
        (value, label)
        for value, label in Candidate._meta.get_field("status").choices
        if value in CANDIDATE_TRANSITIONS.get(candidate.status or "new", set())
    ]
    return render(
        request,
        "hr/candidate_view.html",
        {
            "page_title": f"{candidate.first_name} {candidate.last_name}",
            "candidate": candidate,
            "posting": posting,
            "interviews": interviews,
            "transitions": transitions,
            "can_manage": access.is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def recruitment_candidate_status(request, id):
    return recruitment_candidate_view(request, id)


@login_required
@module_access_required("hr")
def recruitment_interviews(request):
    _hr_staff(request)
    now = timezone.now()
    status_filter = request.GET.get("status") or ""
    queryset = Interview.objects.all()
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    queryset = queryset.order_by("scheduled_date")
    page_obj = paginate(request, queryset, PAGE_SIZE)
    interviews = list(page_obj)
    candidate_map = {
        candidate.id: candidate
        for candidate in Candidate.objects.filter(
            id__in=[interview.candidate_id for interview in interviews]
        )
    }
    job_map = {
        posting.id: posting
        for posting in JobPosting.objects.filter(
            id__in=[interview.job_posting_id for interview in interviews]
        )
    }
    for interview in interviews:
        candidate = candidate_map.get(interview.candidate_id)
        posting = job_map.get(interview.job_posting_id)
        interview.candidate_name = (
            f"{candidate.first_name} {candidate.last_name}" if candidate else "—"
        )
        interview.job_title = posting.job_title if posting else "—"
        interview.is_upcoming = interview.scheduled_date >= now and interview.status in [
            "scheduled",
            "rescheduled",
        ]
    return render(
        request,
        "hr/interviews.html",
        {
            "page_title": "Interviews - SORA ERP",
            "page_obj": page_obj,
            "interviews": interviews,
            "status_filter": status_filter,
            "status_choices": Interview._meta.get_field("status").choices,
            "upcoming_count": Interview.objects.filter(
                scheduled_date__gte=now, status__in=["scheduled", "rescheduled"]
            ).count(),
            "can_manage": get_access(request.user).is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def recruitment_interview_create(request):
    _hr_admin_required(request)
    initial = {}
    if request.GET.get("candidate"):
        candidate = Candidate.objects.filter(pk=request.GET.get("candidate")).first()
        if candidate:
            initial["candidate"] = candidate
            initial["job_posting"] = candidate.job_posting_id
    if request.method == "POST":
        form = InterviewForm(request.POST)
        if form.is_valid():
            interview = form.save(commit=False)
            interview.status = "scheduled"
            interview.created_at = timezone.now()
            interview.save()
            candidate = Candidate.objects.filter(pk=interview.candidate_id).first()
            if candidate and candidate.status in ["new", "screening"]:
                candidate.status = "interview"
                candidate.save(update_fields=["status"])
            messages.success(request, "Interview scheduled.")
            return redirect(f"/hr/recruitment/interview/{interview.id}")
    else:
        form = InterviewForm(initial=initial)
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "Schedule Interview - SORA ERP", "form": form},
    )


@login_required
@module_access_required("hr")
def recruitment_interview_store(request):
    return recruitment_interview_create(request)


@login_required
@module_access_required("hr")
def recruitment_interview_view(request, id):
    access = _hr_staff(request)
    interview = get_object_or_404(Interview, pk=id)
    candidate = Candidate.objects.filter(pk=interview.candidate_id).first()
    posting = JobPosting.objects.filter(pk=interview.job_posting_id).first()
    interviewer = _employees_map([interview.interviewer_id]).get(interview.interviewer_id)
    form = InterviewFeedbackForm(instance=interview)
    if request.method == "POST":
        _hr_admin_required(request)
        form = InterviewFeedbackForm(request.POST, instance=interview)
        if form.is_valid():
            form.save()
            messages.success(request, "Interview feedback saved.")
            return redirect(f"/hr/recruitment/interview/{interview.id}")
    return render(
        request,
        "hr/interview_view.html",
        {
            "page_title": "Interview - SORA ERP",
            "interview": interview,
            "candidate": candidate,
            "posting": posting,
            "interviewer": interviewer,
            "form": form,
            "can_manage": access.is_hr_admin,
        },
    )


@login_required
@module_access_required("hr")
def recruitment_interview_feedback(request, id):
    return recruitment_interview_view(request, id)


# ---------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------
def _payroll_employees():
    return Employee.objects.filter(
        status="active",
        employment_status__in=["active", "probation", "on_leave"],
        salary__gt=0,
    ).order_by("first_name", "last_name")


def _calculate_payroll(period, user):
    working_days = _working_days(period.start_date, period.end_date)
    employees = list(_payroll_employees())
    existing = {
        item.employee_id: item
        for item in PayrollItem.objects.filter(payroll_period_id=period.id)
    }
    kept_ids = []
    now = timezone.now()
    for employee in employees:
        salary = _money(employee.salary)
        attendance = list(
            Attendance.objects.filter(
                employee_id=employee.id,
                date__gte=period.start_date,
                date__lte=period.end_date,
            )
        )
        absent_days = sum(1 for record in attendance if record.status == "absent")
        daily_rate = salary / Decimal(working_days) if working_days else salary
        basic = max(Decimal("0"), salary - daily_rate * Decimal(absent_days))
        overtime_requests = OvertimeRequest.objects.filter(
            employee_id=employee.id,
            overtime_date__gte=period.start_date,
            overtime_date__lte=period.end_date,
            status="approved",
        )
        if overtime_requests.exists():
            overtime_hours = sum((row.hours or Decimal("0")) for row in overtime_requests)
        else:
            overtime_hours = sum((record.overtime_hours or Decimal("0")) for record in attendance)
        hourly_rate = salary / (Decimal(working_days) * STANDARD_HOURS) if working_days else salary / STANDARD_HOURS
        overtime_amount = _money(overtime_hours * hourly_rate * Decimal("1.5"))
        allowances = _parse_amounts(employee.benefits)
        deductions = _parse_amounts(employee.deductions)
        allowances_total = sum((row["amount"] for row in allowances), Decimal("0"))
        other_total = sum((row["amount"] for row in deductions), Decimal("0"))
        bonus = Decimal("0")
        commission = Decimal("0")
        gross = _money(basic + overtime_amount + allowances_total + bonus + commission)
        nssf = _nssf(basic)
        paye = _paye(max(Decimal("0"), gross - nssf))
        nhif = _nhif(gross)
        total_deductions = _money(paye + nssf + nhif + other_total)
        net = _money(max(Decimal("0"), gross - total_deductions))

        item = existing.get(employee.id)
        if item is None:
            item = PayrollItem(
                payroll_period_id=period.id,
                employee_id=employee.id,
                created_at=now,
            )
        item.basic_salary = _money(basic)
        item.overtime_hours = Decimal(str(overtime_hours)).quantize(Decimal("0.01"))
        item.overtime_amount = overtime_amount
        item.allowances = employee.benefits or ""
        item.bonus = bonus
        item.commission = commission
        item.gross_pay = gross
        item.paye_tax = paye
        item.nssf = nssf
        item.nhif = nhif
        item.other_deductions = employee.deductions or ""
        item.total_deductions = total_deductions
        item.net_pay = net
        item.bank_account = employee.bank_account
        item.mobile_money_number = employee.mobile_money_number
        if item.payment_status == "paid":
            pass
        else:
            item.payment_status = "pending"
            item.payment_reference = ""
            item.paid_at = None
        item.save()
        kept_ids.append(item.id)

    PayrollItem.objects.filter(payroll_period_id=period.id).exclude(id__in=kept_ids).delete()
    totals = PayrollItem.objects.filter(payroll_period_id=period.id).aggregate(
        gross=Sum("gross_pay"),
        net=Sum("net_pay"),
        deductions=Sum("total_deductions"),
        tax=Sum("paye_tax"),
    )
    period.total_gross = totals["gross"] or Decimal("0")
    period.total_net = totals["net"] or Decimal("0")
    period.total_deductions = totals["deductions"] or Decimal("0")
    period.total_tax = totals["tax"] or Decimal("0")
    period.status = "processing"
    period.processed_by = user.id
    period.processed_at = now
    period.save()
    return len(kept_ids)


@login_required
@module_access_required("hr")
def payroll_index(request):
    access = _hr_staff(request)
    periods = list(PayrollPeriod.objects.order_by("-start_date"))
    item_counts = {
        row["payroll_period_id"]: row["count"]
        for row in PayrollItem.objects.values("payroll_period_id").annotate(count=Count("id"))
    }
    for period in periods:
        period.item_count = item_counts.get(period.id, 0)
        period.is_editable = period.status in ["draft", "processing"]
    latest = periods[0] if periods else None
    stats = {
        "periods": len(periods),
        "employees": _payroll_employees().count(),
        "latest_net": latest.total_net if latest else Decimal("0"),
        "latest_label": latest.period_name if latest else "No periods yet",
        "paid": PayrollItem.objects.filter(payment_status="paid").count(),
    }
    return render(
        request,
        "hr/payroll.html",
        {
            "page_title": "Payroll - SORA ERP",
            "periods": periods,
            "stats": stats,
            "can_manage": access.is_hr_admin or access.is_admin,
        },
    )


@login_required
@module_access_required("hr")
def payroll_create_period(request):
    _hr_admin_required(request)
    today = timezone.localdate()
    if request.method == "POST":
        form = PayrollPeriodForm(request.POST)
        if form.is_valid():
            period = form.save(commit=False)
            if PayrollPeriod.objects.filter(
                start_date__lte=period.end_date, end_date__gte=period.start_date
            ).exists():
                messages.error(request, "A payroll period already overlaps those dates.")
            else:
                period.status = "draft"
                period.created_at = timezone.now()
                period.save()
                messages.success(request, "Payroll period created.")
                return redirect(f"/hr/payroll/process/{period.id}")
    else:
        form = PayrollPeriodForm(
            initial={
                "period_name": f"{today.strftime('%B %Y')} Payroll",
                "start_date": today.replace(day=1),
                "end_date": today,
                "payment_date": today,
            }
        )
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Payroll Period - SORA ERP", "form": form},
    )


@login_required
@module_access_required("hr")
def payroll_store_period(request):
    return payroll_create_period(request)


@login_required
@module_access_required("hr")
def payroll_process(request, id):
    access = _hr_admin_required(request)
    period = get_object_or_404(PayrollPeriod, pk=id)
    employees = list(_payroll_employees())
    working_days = _working_days(period.start_date, period.end_date)
    estimated_salary = sum((employee.salary or Decimal("0")) for employee in employees)
    existing_items = PayrollItem.objects.filter(payroll_period_id=period.id).count()
    return render(
        request,
        "hr/payroll_process.html",
        {
            "page_title": f"Process {period.period_name} - SORA ERP",
            "period": period,
            "employees": employees,
            "employee_count": len(employees),
            "working_days": working_days,
            "estimated_salary": estimated_salary,
            "existing_items": existing_items,
            "can_calculate": period.status in ["draft", "processing"],
            "can_approve": period.status == "processing",
            "can_pay": period.status == "approved",
        },
    )


@login_required
@module_access_required("hr")
def payroll_calculate(request, id):
    _hr_admin_required(request)
    if request.method != "POST":
        return redirect(f"/hr/payroll/process/{id}")
    period = get_object_or_404(PayrollPeriod, pk=id)
    if period.status not in ["draft", "processing"]:
        messages.error(request, "Approved or paid periods cannot be recalculated.")
        return redirect(f"/hr/payroll/view/{period.id}")
    with transaction.atomic():
        count = _calculate_payroll(period, request.user)
    messages.success(request, f"Payroll calculated for {count} employee(s).")
    return redirect(f"/hr/payroll/view/{period.id}")


@login_required
@module_access_required("hr")
def payroll_view(request, id):
    access = _hr_staff(request)
    period = get_object_or_404(PayrollPeriod, pk=id)
    items = list(PayrollItem.objects.filter(payroll_period_id=period.id).order_by("employee_id"))
    employees = _employees_map([item.employee_id for item in items])
    for item in items:
        item.employee = employees.get(item.employee_id)
        item.allowance_rows = _parse_amounts(item.allowances)
        item.deduction_rows = _parse_amounts(item.other_deductions)
    return render(
        request,
        "hr/payroll_view.html",
        {
            "page_title": f"Payroll {period.period_name} - SORA ERP",
            "period": period,
            "items": items,
            "can_approve": period.status == "processing" and (access.is_hr_admin or access.is_admin),
            "can_pay": period.status == "approved" and (access.is_hr_admin or access.is_admin),
            "can_recalculate": period.status in ["draft", "processing"] and (access.is_hr_admin or access.is_admin),
        },
    )


@login_required
@module_access_required("hr")
def payroll_approve(request, id):
    _hr_admin_required(request)
    period = get_object_or_404(PayrollPeriod, pk=id)
    if request.method == "POST":
        if period.status != "processing":
            messages.error(request, "Only processed periods can be approved.")
        elif not PayrollItem.objects.filter(payroll_period_id=period.id).exists():
            messages.error(request, "Calculate payroll before approving.")
        else:
            period.status = "approved"
            period.approved_by = request.user.id
            period.approved_at = timezone.now()
            period.save(update_fields=["status", "approved_by", "approved_at"])
            messages.success(request, "Payroll approved.")
    return redirect(f"/hr/payroll/view/{period.id}")


@login_required
@module_access_required("hr")
def payroll_mark_paid(request, id):
    _hr_admin_required(request)
    period = get_object_or_404(PayrollPeriod, pk=id)
    if request.method == "POST":
        if period.status != "approved":
            messages.error(request, "Only approved payroll can be marked paid.")
        else:
            now = timezone.now()
            reference = f"PAY-{period.id:04d}-{now.strftime('%Y%m%d')}"
            PayrollItem.objects.filter(payroll_period_id=period.id).update(
                payment_status="paid", paid_at=now, payment_reference=reference
            )
            period.status = "paid"
            period.save(update_fields=["status"])
            messages.success(request, "Payroll marked as paid.")
    return redirect(f"/hr/payroll/view/{period.id}")


@login_required
@module_access_required("hr")
def payroll_payslip(request, employeeId, periodId):
    access = get_access(request.user)
    item = get_object_or_404(
        PayrollItem, employee_id=employeeId, payroll_period_id=periodId
    )
    employee = get_object_or_404(Employee, pk=employeeId)
    own = _employee_for_user(request.user)
    if own is None or own.id != employee.id:
        if not (access.is_admin or access.is_hr_admin or access.is_hr_viewer):
            raise PermissionDenied("You cannot view this payslip.")
    period = get_object_or_404(PayrollPeriod, pk=periodId)
    company = get_company_settings()
    allowance_rows = _parse_amounts(item.allowances)
    deduction_rows = _parse_amounts(item.other_deductions)
    return render(
        request,
        "hr/payslip.html",
        {
            "page_title": f"Payslip {employee.employee_code} - {period.period_name}",
            "item": item,
            "employee": employee,
            "period": period,
            "company": company,
            "currency_symbol": get_currency_symbol(company),
            "allowance_rows": allowance_rows,
            "deduction_rows": deduction_rows,
            "net_words": _amount_in_words(item.net_pay),
            "allowance_total": sum((row["amount"] for row in allowance_rows), Decimal("0")),
            "deduction_total": sum((row["amount"] for row in deduction_rows), Decimal("0")),
        },
    )


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def recognition_index(request):
    access = get_access(request.user)
    today = timezone.localdate()
    awards = list(EmployeeRecognition.objects.order_by("-created_at")[:12])
    people = _employees_map(
        [award.employee_id for award in awards] + [award.given_by for award in awards]
    )
    badges = {badge.id: badge for badge in RecognitionBadge.objects.all()}
    for award in awards:
        employee = people.get(award.employee_id)
        giver = people.get(award.given_by)
        award.employee_name = (
            f"{employee.first_name} {employee.last_name}" if employee else "—"
        )
        award.giver_name = f"{giver.first_name} {giver.last_name}" if giver else "—"
        award.badge = badges.get(award.badge_id)
        award.badge_name = award.badge.badge_name if award.badge else "Badge"
        award.badge_icon = award.badge.badge_icon if award.badge else "fa-award"
    year_awards = EmployeeRecognition.objects.filter(created_at__year=today.year)
    stats = {
        "awards": EmployeeRecognition.objects.count(),
        "month": EmployeeRecognition.objects.filter(
            created_at__year=today.year, created_at__month=today.month
        ).count(),
        "points": year_awards.aggregate(total=Sum("points_earned"))["total"] or 0,
        "people": year_awards.values("employee_id").distinct().count(),
    }
    return render(
        request,
        "hr/recognition.html",
        {
            "page_title": "Recognition - SORA ERP",
            "awards": awards,
            "stats": stats,
            "can_give": _employee_for_user(request.user) is not None,
            "access": access,
        },
    )


@login_required
@module_access_required("hr")
def recognition_badges(request):
    access = get_access(request.user)
    badges = list(RecognitionBadge.objects.order_by("-points", "badge_name"))
    counts = {
        row["badge_id"]: {"awards": row["awards"], "points": row["points"]}
        for row in EmployeeRecognition.objects.values("badge_id").annotate(
            awards=Count("id"), points=Sum("points_earned")
        )
    }
    for badge in badges:
        badge.award_count = counts.get(badge.id, {}).get("awards", 0)
        badge.points_awarded = counts.get(badge.id, {}).get("points", 0)
    return render(
        request,
        "hr/recognition_badges.html",
        {
            "page_title": "Recognition Badges - SORA ERP",
            "badges": badges,
            "can_give": _employee_for_user(request.user) is not None,
            "access": access,
        },
    )


@login_required
@module_access_required("hr")
def recognition_give(request):
    employee = _employee_for_user(request.user)
    if employee is None:
        messages.error(request, "Your user account is not linked to an employee record.")
        return redirect("/hr/employee/dashboard")
    if request.method == "POST":
        form = RecognitionGiveForm(request.POST)
        if form.is_valid():
            recipient = form.cleaned_data["employee"]
            badge = form.cleaned_data["badge"]
            if recipient.id == employee.id:
                messages.error(request, "You cannot award recognition to yourself.")
            else:
                EmployeeRecognition.objects.create(
                    employee_id=recipient.id,
                    badge_id=badge.id,
                    given_by=employee.id,
                    reason=form.cleaned_data.get("reason") or "",
                    message=form.cleaned_data.get("message") or "",
                    points_earned=badge.points or 0,
                    created_at=timezone.now(),
                )
                messages.success(request, f"{badge.badge_name} awarded to {recipient.first_name}.")
                return redirect("/hr/recognition")
    else:
        form = RecognitionGiveForm()
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "Give Recognition - SORA ERP", "form": form},
    )


@login_required
@module_access_required("hr")
def recognition_store(request):
    return recognition_give(request)


@login_required
@module_access_required("hr")
def recognition_leaderboard(request):
    access = get_access(request.user)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (ValueError, TypeError):
        year = today.year
    rows = list(
        EmployeeRecognition.objects.filter(created_at__year=year)
        .values("employee_id")
        .annotate(points=Sum("points_earned"), awards=Count("id"))
        .order_by("-points", "-awards")
    )
    employees = _employees_map([row["employee_id"] for row in rows])
    departments = _departments_map()
    for row in rows:
        employee = employees.get(row["employee_id"])
        row["employee"] = employee
        row["name"] = f"{employee.first_name} {employee.last_name}" if employee else "—"
        row["department"] = getattr(
            departments.get(employee.department_id if employee else None), "department_name", "—"
        )
    return render(
        request,
        "hr/recognition_leaderboard.html",
        {
            "page_title": "Recognition Leaderboard - SORA ERP",
            "rows": rows,
            "year": year,
            "years": range(today.year - 2, today.year + 1),
            "can_give": _employee_for_user(request.user) is not None,
            "access": access,
        },
    )


# ---------------------------------------------------------------------------
# Wellness
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def wellness_index(request):
    employee = _require_employee(request)
    if employee is None:
        return redirect("/hr/employee/dashboard")
    today = timezone.localdate()
    latest = WellnessCheckin.objects.filter(employee_id=employee.id).order_by("-checkin_date").first()
    records = list(
        WellnessCheckin.objects.filter(
            employee_id=employee.id, checkin_date__gte=today - timedelta(days=29)
        ).order_by("checkin_date")
    )
    averages = WellnessCheckin.objects.filter(
        employee_id=employee.id, checkin_date__gte=today - timedelta(days=29)
    ).aggregate(mood=Avg("mood_score"), stress=Avg("stress_level"), energy=Avg("energy_level"))
    streak = 0
    cursor = today
    checked_dates = {
        row.checkin_date
        for row in WellnessCheckin.objects.filter(
            employee_id=employee.id, checkin_date__gte=today - timedelta(days=60)
        )
    }
    if today not in checked_dates and (today - timedelta(days=1)) in checked_dates:
        cursor = today - timedelta(days=1)
    while cursor in checked_dates:
        streak += 1
        cursor -= timedelta(days=1)
    max_mood = max([row.mood_score or 0 for row in records] + [10])
    return render(
        request,
        "hr/wellness.html",
        {
            "page_title": "Wellness - SORA ERP",
            "employee": employee,
            "latest": latest,
            "records": records,
            "averages": averages,
            "streak": streak,
            "checked_in_today": today in checked_dates,
            "max_mood": max_mood,
            "access": get_access(request.user),
        },
    )


@login_required
@module_access_required("hr")
def wellness_checkin(request):
    employee = _require_employee(request)
    if employee is None:
        return redirect("/hr/employee/dashboard")
    today = timezone.localdate()
    existing = WellnessCheckin.objects.filter(
        employee_id=employee.id, checkin_date=today
    ).first()
    if request.method == "POST":
        form = WellnessCheckinForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.employee_id = employee.id
            record.checkin_date = today
            if existing is None:
                record.created_at = timezone.now()
                record.save()
            else:
                for field in ["mood_score", "stress_level", "energy_level", "sleep_hours", "notes"]:
                    setattr(existing, field, getattr(record, field))
                existing.save()
            messages.success(request, "Wellness check-in saved.")
            return redirect("/hr/wellness")
    else:
        form = WellnessCheckinForm(instance=existing)
    return render(
        request,
        "hr/form_page.html",
        {
            "page_title": "Wellness Check-in - SORA ERP",
            "form": form,
            "existing": existing,
            "today": today,
        },
    )


@login_required
@module_access_required("hr")
def wellness_store_checkin(request):
    return wellness_checkin(request)


@login_required
@module_access_required("hr")
def wellness_team(request):
    access = _hr_staff(request)
    today = timezone.localdate()
    since = today - timedelta(days=13)
    rows = list(
        WellnessCheckin.objects.filter(checkin_date__gte=since)
        .values("employee_id")
        .annotate(
            mood=Avg("mood_score"),
            stress=Avg("stress_level"),
            energy=Avg("energy_level"),
            checkins=Count("id"),
            last_checkin=Max("checkin_date"),
        )
    )
    employees = _employees_map([row["employee_id"] for row in rows])
    departments = _departments_map()
    dept_rows = {}
    at_risk = []
    active_count = Employee.objects.filter(
        status="active", employment_status__in=["active", "probation", "on_leave"]
    ).count()
    checked_in_week = set()
    for row in rows:
        employee = employees.get(row["employee_id"])
        if employee is None:
            continue
        department = departments.get(employee.department_id)
        dept_name = department.department_name if department else "Unassigned"
        bucket = dept_rows.setdefault(
            dept_name, {"mood": [], "stress": [], "people": set(), "checkins": 0}
        )
        if row["mood"] is not None:
            bucket["mood"].append(float(row["mood"]))
        if row["stress"] is not None:
            bucket["stress"].append(float(row["stress"]))
        bucket["people"].add(employee.id)
        bucket["checkins"] += row["checkins"]
        if row["last_checkin"] and row["last_checkin"] >= today - timedelta(days=6):
            checked_in_week.add(employee.id)
        if (row["mood"] is not None and row["mood"] <= 3) or (
            row["stress"] is not None and row["stress"] >= 7
        ):
            at_risk.append(
                {
                    "employee": employee,
                    "department": dept_name,
                    "mood": row["mood"],
                    "stress": row["stress"],
                    "energy": row["energy"],
                    "last_checkin": row["last_checkin"],
                }
            )
    comparison = []
    for name, bucket in dept_rows.items():
        comparison.append(
            {
                "department": name,
                "mood": round(sum(bucket["mood"]) / len(bucket["mood"]), 1) if bucket["mood"] else 0,
                "stress": round(sum(bucket["stress"]) / len(bucket["stress"]), 1)
                if bucket["stress"]
                else 0,
                "people": len(bucket["people"]),
                "checkins": bucket["checkins"],
            }
        )
    comparison.sort(key=lambda item: item["mood"], reverse=True)
    participation = round(len(checked_in_week) / active_count * 100, 1) if active_count else 0
    return render(
        request,
        "hr/wellness_team.html",
        {
            "page_title": "Team Wellness - SORA ERP",
            "comparison": comparison,
            "at_risk": at_risk,
            "participation": participation,
            "checked_in_week": len(checked_in_week),
            "active_count": active_count,
            "window_start": since,
            "today": today,
        },
    )


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def training_index(request):
    access = get_access(request.user)
    employee = _employee_for_user(request.user)
    programs = list(TrainingProgram.objects.order_by("program_name"))
    counts = {
        row["training_id"]: row["count"]
        for row in EmployeeTraining.objects.values("training_id").annotate(count=Count("id"))
    }
    mine = {}
    if employee is not None:
        mine = {
            row.training_id: row
            for row in EmployeeTraining.objects.filter(employee_id=employee.id)
        }
    for program in programs:
        program.enrollment_count = counts.get(program.id, 0)
        program.my_enrollment = mine.get(program.id)
    stats = {
        "programs": len(programs),
        "active": sum(1 for program in programs if program.status == "active"),
        "enrollments": EmployeeTraining.objects.count(),
        "completed": EmployeeTraining.objects.filter(status="completed").count(),
    }
    return render(
        request,
        "hr/training.html",
        {
            "page_title": "Training - SORA ERP",
            "programs": programs,
            "stats": stats,
            "employee": employee,
            "can_manage": access.is_hr_admin or access.is_admin,
        },
    )


@login_required
@module_access_required("hr")
def training_create(request):
    _hr_admin_required(request)
    if request.method == "POST":
        form = TrainingProgramForm(request.POST)
        if form.is_valid():
            program = form.save(commit=False)
            program.program_code = next_code("TRN", "training_programs", "program_code")
            program.created_at = timezone.now()
            program.save()
            messages.success(request, "Training programme created.")
            return redirect("/hr/training")
    else:
        form = TrainingProgramForm()
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Training Programme - SORA ERP", "form": form},
    )


@login_required
@module_access_required("hr")
def training_store(request):
    return training_create(request)


@login_required
@module_access_required("hr")
def training_my(request):
    employee = _require_employee(request)
    if employee is None:
        return redirect("/hr/employee/dashboard")
    rows = list(EmployeeTraining.objects.filter(employee_id=employee.id).order_by("-created_at"))
    programs = {
        program.id: program
        for program in TrainingProgram.objects.filter(
            id__in=[row.training_id for row in rows]
        )
    }
    for row in rows:
        row.program = programs.get(row.training_id)
        row.program_name = row.program.program_name if row.program else "Training"
    return render(
        request,
        "hr/training_my.html",
        {"page_title": "My Trainings - SORA ERP", "rows": rows},
    )


@login_required
@module_access_required("hr")
def training_enroll(request):
    employee = _require_employee(request)
    if employee is None:
        return redirect("/hr/employee/dashboard")
    if request.method == "POST":
        program = get_object_or_404(TrainingProgram, pk=request.POST.get("program_id"))
        if program.status != "active":
            messages.error(request, "This programme is not open for enrolment.")
        else:
            enrollment, created = EmployeeTraining.objects.get_or_create(
                employee_id=employee.id,
                training_id=program.id,
                defaults={"status": "enrolled", "created_at": timezone.now()},
            )
            if created:
                messages.success(request, f"Enrolled in {program.program_name}.")
            else:
                messages.info(request, "You are already enrolled in this programme.")
    return redirect(request.POST.get("next") or "/hr/training")


@login_required
@module_access_required("hr")
def training_complete(request, id):
    access = get_access(request.user)
    enrollment = get_object_or_404(EmployeeTraining, pk=id)
    employee = _employee_for_user(request.user)
    if employee is None or enrollment.employee_id != employee.id:
        if not (access.is_hr_admin or access.is_admin):
            raise PermissionDenied("You cannot update this enrolment.")
    if request.method != "POST":
        return redirect("/hr/training/my-trainings")
    if enrollment.status == "completed":
        messages.warning(request, "This training is already completed.")
        return redirect(request.POST.get("next") or "/hr/training/my-trainings")
    try:
        score = Decimal(request.POST.get("score") or "0")
    except Exception:
        score = None
    if score is None or not (Decimal("0") <= score <= Decimal("100")):
        messages.error(request, "Score must be between 0 and 100.")
        return redirect(request.POST.get("next") or "/hr/training/my-trainings")
    completion_raw = request.POST.get("completion_date") or timezone.localdate().isoformat()
    try:
        completion_date = datetime.strptime(completion_raw, "%Y-%m-%d").date()
    except ValueError:
        completion_date = timezone.localdate()
    enrollment.status = "completed"
    enrollment.score = score
    enrollment.completion_date = completion_date
    enrollment.save(update_fields=["status", "score", "completion_date"])
    messages.success(request, "Training marked completed.")
    return redirect(request.POST.get("next") or "/hr/training/my-trainings")


# ---------------------------------------------------------------------------
# Succession planning
# ---------------------------------------------------------------------------
@login_required
@module_access_required("hr")
def succession_index(request):
    access = _hr_staff(request)
    plans = list(SuccessionPlan.objects.all())
    positions = _positions_map([plan.position_id for plan in plans])
    employees = _employees_map(
        [plan.current_holder_id for plan in plans]
        + [
            successor_id
            for plan in plans
            for successor_id in _successor_ids(plan)
        ]
    )
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    plans.sort(key=lambda plan: risk_order.get(plan.risk_level or "medium", 9))
    for plan in plans:
        plan.position = positions.get(plan.position_id)
        holder = employees.get(plan.current_holder_id)
        plan.holder_name = f"{holder.first_name} {holder.last_name}" if holder else "—"
        plan.successors = [
            employees[successor_id]
            for successor_id in _successor_ids(plan)
            if successor_id in employees
        ]
    stats = {
        "plans": len(plans),
        "critical": sum(1 for plan in plans if plan.risk_level == "critical"),
        "high": sum(1 for plan in plans if plan.risk_level == "high"),
        "ready_now": sum(1 for plan in plans if plan.readiness_level == "ready_now"),
    }
    return render(
        request,
        "hr/succession.html",
        {
            "page_title": "Succession Planning - SORA ERP",
            "plans": plans,
            "stats": stats,
            "can_manage": access.is_hr_admin or access.is_admin,
        },
    )


def _successor_ids(plan):
    try:
        ids = json.loads(plan.potential_successors or "[]")
    except ValueError:
        return []
    if not isinstance(ids, list):
        return []
    result = []
    for value in ids:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


@login_required
@module_access_required("hr")
def succession_create(request):
    _hr_admin_required(request)
    if request.method == "POST":
        form = SuccessionPlanForm(request.POST)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.updated_at = timezone.now()
            plan.save()
            messages.success(request, "Succession plan created.")
            return redirect(f"/hr/succession/view/{plan.id}")
    else:
        form = SuccessionPlanForm()
    return render(
        request,
        "hr/form_page.html",
        {"page_title": "New Succession Plan - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("hr")
def succession_store(request):
    return succession_create(request)


@login_required
@module_access_required("hr")
def succession_edit(request, id):
    _hr_admin_required(request)
    plan = get_object_or_404(SuccessionPlan, pk=id)
    if request.method == "POST":
        form = SuccessionPlanForm(request.POST, instance=plan)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.updated_at = timezone.now()
            plan.save()
            messages.success(request, "Succession plan updated.")
            return redirect(f"/hr/succession/view/{plan.id}")
    else:
        form = SuccessionPlanForm(instance=plan)
    return render(
        request,
        "hr/form_page.html",
        {
            "page_title": "Edit Succession Plan - SORA ERP",
            "form": form,
            "plan": plan,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("hr")
def succession_update(request, id):
    return succession_edit(request, id)


@login_required
@module_access_required("hr")
def succession_view(request, id):
    access = _hr_staff(request)
    plan = get_object_or_404(SuccessionPlan, pk=id)
    position = _positions_map([plan.position_id]).get(plan.position_id)
    employee_ids = [plan.current_holder_id] + _successor_ids(plan)
    people = _employees_map(employee_ids)
    holder = people.get(plan.current_holder_id)
    successors = [
        people[successor_id] for successor_id in _successor_ids(plan) if successor_id in people
    ]
    return render(
        request,
        "hr/succession_view.html",
        {
            "page_title": "Succession Plan - SORA ERP",
            "plan": plan,
            "position": position,
            "holder": holder,
            "successors": successors,
            "can_manage": access.is_hr_admin,
        },
    )


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def _headcount_on(day):
    return (
        Employee.objects.filter(hire_date__lte=day)
        .exclude(
            employment_status__in=["terminated", "resigned", "retired"],
            termination_date__isnull=True,
        )
        .exclude(termination_date__lte=day)
        .count()
    )


@login_required
@module_access_required("hr")
def analytics_index(request):
    _hr_staff(request)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (ValueError, TypeError):
        year = today.year
    if year < 2000 or year > today.year + 1:
        year = today.year
    year_end = min(date(year, 12, 31), today)
    start_count = _headcount_on(date(year, 1, 1) - timedelta(days=1))
    end_count = _headcount_on(year_end)
    new_hires = Employee.objects.filter(hire_date__year=year).count()
    terminations = Employee.objects.filter(termination_date__year=year).count()
    average_headcount = (start_count + end_count) / 2 if (start_count + end_count) else 0
    turnover = round(terminations / average_headcount * 100, 1) if average_headcount else 0
    growth = round((end_count - start_count) / start_count * 100, 1) if start_count else 0
    active = Employee.objects.filter(
        status="active", employment_status__in=["active", "probation", "on_leave"]
    )
    department_counts = {
        row["department_id"]: row["count"]
        for row in active.values("department_id").annotate(count=Count("id"))
    }
    departments = _departments_map()
    distribution = []
    for department_id, count in sorted(
        department_counts.items(), key=lambda item: item[1], reverse=True
    ):
        department = departments.get(department_id)
        distribution.append(
            {
                "name": department.department_name if department else "Unassigned",
                "count": count,
            }
        )
    month_rows = []
    for month in range(1, 13):
        month_end = min(date(year, month, calendar.monthrange(year, month)[1]), today)
        if date(year, month, 1) > today:
            month_rows.append({"label": calendar.month_abbr[month], "count": 0})
        else:
            month_rows.append({"label": calendar.month_abbr[month], "count": _headcount_on(month_end)})
    max_headcount = max([row["count"] for row in month_rows] + [1])
    gender_counts = {
        row["gender"] or "unspecified": row["count"]
        for row in active.values("gender").annotate(count=Count("id"))
    }
    type_counts = {
        row["employee_type"] or "unspecified": row["count"]
        for row in active.values("employee_type").annotate(count=Count("id"))
    }
    engagement = WellnessCheckin.objects.filter(
        checkin_date__gte=today - timedelta(days=29)
    ).aggregate(mood=Avg("mood_score"), stress=Avg("stress_level"))
    latest_period = PayrollPeriod.objects.exclude(status="draft").order_by("-start_date").first()
    open_jobs = JobPosting.objects.filter(status="published").count()
    applications = Candidate.objects.filter(created_at__year=year).count()
    return render(
        request,
        "hr/analytics.html",
        {
            "page_title": "HR Analytics - SORA ERP",
            "year": year,
            "years": range(today.year - 3, today.year + 1),
            "stats": {
                "headcount": active.count(),
                "new_hires": new_hires,
                "terminations": terminations,
                "turnover": turnover,
                "growth": growth,
                "start_count": start_count,
                "end_count": end_count,
            },
            "distribution": distribution,
            "distribution_max": max([row["count"] for row in distribution] + [1]),
            "month_rows": month_rows,
            "max_headcount": max_headcount,
            "gender_counts": gender_counts,
            "type_counts": type_counts,
            "engagement": engagement,
            "latest_period": latest_period,
            "open_jobs": open_jobs,
            "applications": applications,
        },
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _report_employees(request):
    queryset = Employee.objects.all()
    department = request.GET.get("department") or ""
    status_filter = request.GET.get("status") or ""
    if department:
        queryset = queryset.filter(department_id=department)
    if status_filter:
        queryset = queryset.filter(employment_status=status_filter)
    departments = _departments_map()
    headers = [
        "Code", "Name", "Email", "Phone", "Department", "Job title",
        "Employment status", "Hire date", "Salary",
    ]
    rows = []
    for employee in queryset.order_by("first_name", "last_name"):
        department_row = departments.get(employee.department_id)
        rows.append(
            [
                employee.employee_code,
                f"{employee.first_name} {employee.last_name}",
                employee.email,
                employee.phone or "",
                getattr(department_row, "department_name", ""),
                employee.job_title or "",
                employee.get_employment_status_display() or "",
                employee.hire_date.isoformat() if employee.hire_date else "",
                str(employee.salary or ""),
            ]
        )
    summary = [
        {"label": "Employees", "value": queryset.count()},
        {
            "label": "Active",
            "value": queryset.filter(
                employment_status__in=["active", "probation", "on_leave"]
            ).count(),
        },
        {
            "label": "Payroll cost",
            "value": f"{get_currency_symbol()} {queryset.aggregate(total=Sum('salary'))['total'] or 0:,.2f}",
        },
    ]
    return headers, rows, summary


def _report_leave(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (ValueError, TypeError):
        year = today.year
    status_filter = request.GET.get("status") or ""
    queryset = LeaveRequest.objects.filter(start_date__year=year)
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    employees = _employees_map([row.employee_id for row in queryset])
    types = _leave_types_map()
    headers = ["Employee", "Leave type", "Start", "End", "Days", "Status", "Requested"]
    rows = []
    for leave in queryset.order_by("-start_date"):
        employee = employees.get(leave.employee_id)
        leave_type = types.get(leave.leave_type_id)
        rows.append(
            [
                f"{employee.first_name} {employee.last_name}" if employee else "—",
                getattr(leave_type, "leave_name", ""),
                leave.start_date.isoformat(),
                leave.end_date.isoformat(),
                str(leave.duration_days),
                leave.get_status_display() or "",
                timezone.localtime(leave.created_at).strftime("%Y-%m-%d") if leave.created_at else "",
            ]
        )
    summary = [
        {"label": "Requests", "value": queryset.count()},
        {
            "label": "Approved days",
            "value": queryset.filter(status="approved").aggregate(total=Sum("duration_days"))["total"]
            or 0,
        },
        {"label": "Pending", "value": queryset.filter(status="pending").count()},
    ]
    return headers, rows, summary


def _report_attendance(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
        if not 1 <= month <= 12:
            raise ValueError
    except (ValueError, TypeError):
        year, month = today.year, today.month
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    department = request.GET.get("department") or ""
    employees = Employee.objects.exclude(
        employment_status__in=["terminated", "resigned", "retired"]
    )
    if department:
        employees = employees.filter(department_id=department)
    employees = list(employees.order_by("first_name", "last_name"))
    departments = _departments_map()
    aggregates = {
        row["employee_id"]: row
        for row in Attendance.objects.filter(
            employee_id__in=[employee.id for employee in employees],
            date__gte=start,
            date__lte=end,
        )
        .values("employee_id")
        .annotate(
            days=Count("id"),
            hours=Sum("total_hours"),
            overtime=Sum("overtime_hours"),
            late=Count("id", filter=Q(status="late")),
            absent=Count("id", filter=Q(status="absent")),
        )
    }
    headers = ["Employee", "Department", "Days recorded", "Hours", "Overtime", "Late", "Absent"]
    rows = []
    for employee in employees:
        aggregate = aggregates.get(employee.id, {})
        rows.append(
            [
                f"{employee.first_name} {employee.last_name}",
                getattr(departments.get(employee.department_id), "department_name", ""),
                aggregate.get("days") or 0,
                f"{aggregate.get('hours') or Decimal('0'):.2f}",
                f"{aggregate.get('overtime') or Decimal('0'):.2f}",
                aggregate.get("late") or 0,
                aggregate.get("absent") or 0,
            ]
        )
    summary = [
        {"label": "Month", "value": start.strftime("%B %Y")},
        {"label": "Employees", "value": len(employees)},
        {"label": "Hours", "value": f"{sum(Decimal(str(row.get('hours') or 0)) for row in aggregates.values()):,.2f}"},
    ]
    return headers, rows, summary


def _report_performance(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (ValueError, TypeError):
        year = today.year
    queryset = PerformanceReview.objects.filter(review_period_start__year=year)
    employees = _employees_map(
        [row.employee_id for row in queryset] + [row.reviewer_id for row in queryset]
    )
    headers = ["Employee", "Reviewer", "Period", "Type", "Rating", "Status"]
    rows = []
    for review in queryset.order_by("-review_period_end"):
        employee = employees.get(review.employee_id)
        reviewer = employees.get(review.reviewer_id)
        rows.append(
            [
                f"{employee.first_name} {employee.last_name}" if employee else "—",
                f"{reviewer.first_name} {reviewer.last_name}" if reviewer else "—",
                f"{review.review_period_start} – {review.review_period_end}",
                review.get_review_type_display() or "",
                str(review.overall_rating or ""),
                review.get_status_display() or "",
            ]
        )
    summary = [
        {"label": "Reviews", "value": queryset.count()},
        {
            "label": "Average rating",
            "value": round(queryset.aggregate(value=Avg("overall_rating"))["value"] or 0, 2),
        },
        {
            "label": "Goals completed",
            "value": Goal.objects.filter(status="completed", target_date__year=year).count(),
        },
    ]
    return headers, rows, summary


def _report_recruitment(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (ValueError, TypeError):
        year = today.year
    postings = list(JobPosting.objects.filter(posting_date__year=year).order_by("-posting_date"))
    departments = _departments_map()
    candidate_counts = {
        row["job_posting_id"]: row["count"]
        for row in Candidate.objects.filter(
            job_posting_id__in=[posting.id for posting in postings]
        )
        .values("job_posting_id")
        .annotate(count=Count("id"))
    }
    interview_counts = {
        row["job_posting_id"]: row["count"]
        for row in Interview.objects.filter(
            job_posting_id__in=[posting.id for posting in postings]
        )
        .values("job_posting_id")
        .annotate(count=Count("id"))
    }
    headers = ["Job title", "Department", "Status", "Posted", "Closing", "Candidates", "Interviews"]
    rows = []
    for posting in postings:
        rows.append(
            [
                posting.job_title,
                getattr(departments.get(posting.department_id), "department_name", ""),
                posting.get_status_display() or "",
                posting.posting_date.isoformat(),
                posting.closing_date.isoformat() if posting.closing_date else "",
                candidate_counts.get(posting.id, 0),
                interview_counts.get(posting.id, 0),
            ]
        )
    summary = [
        {"label": "Openings", "value": len(postings)},
        {"label": "Applications", "value": Candidate.objects.filter(created_at__year=year).count()},
        {"label": "Hired", "value": Candidate.objects.filter(status="hired", created_at__year=year).count()},
    ]
    return headers, rows, summary


def _report_payroll(request):
    period_id = request.GET.get("period") or ""
    periods = PayrollPeriod.objects.order_by("-start_date")
    period = periods.filter(pk=period_id).first() if period_id else periods.first()
    headers = [
        "Employee", "Basic", "Overtime", "Allowances", "Gross", "PAYE", "NSSF", "NHIF", "Net", "Status",
    ]
    rows = []
    if period:
        items = list(PayrollItem.objects.filter(payroll_period_id=period.id).order_by("employee_id"))
        employees = _employees_map([item.employee_id for item in items])
        for item in items:
            employee = employees.get(item.employee_id)
            rows.append(
                [
                    f"{employee.first_name} {employee.last_name}" if employee else f"#{item.employee_id}",
                    f"{item.basic_salary:.2f}",
                    f"{item.overtime_amount or 0:.2f}",
                    f"{sum((row['amount'] for row in _parse_amounts(item.allowances)), Decimal('0')):.2f}",
                    f"{item.gross_pay:.2f}",
                    f"{item.paye_tax or 0:.2f}",
                    f"{item.nssf or 0:.2f}",
                    f"{item.nhif or 0:.2f}",
                    f"{item.net_pay:.2f}",
                    item.get_payment_status_display() or "",
                ]
            )
    summary = [
        {"label": "Period", "value": period.period_name if period else "No payroll periods"},
        {"label": "Gross", "value": f"{period.total_gross if period else 0}"},
        {"label": "Net", "value": f"{period.total_net if period else 0}"},
    ]
    return headers, rows, summary


REPORT_BUILDERS = {
    "employees": _report_employees,
    "leave": _report_leave,
    "attendance": _report_attendance,
    "performance": _report_performance,
    "recruitment": _report_recruitment,
    "payroll": _report_payroll,
}


@login_required
@module_access_required("hr")
def reports_index(request):
    _hr_staff(request)
    today = timezone.localdate()
    stats = {
        "employees": Employee.objects.count(),
        "leave_requests": LeaveRequest.objects.filter(start_date__year=today.year).count(),
        "reviews": PerformanceReview.objects.filter(review_period_start__year=today.year).count(),
        "jobs": JobPosting.objects.filter(posting_date__year=today.year).count(),
        "periods": PayrollPeriod.objects.count(),
        "attendance": Attendance.objects.filter(
            date__year=today.year, date__month=today.month
        ).count(),
    }
    return render(
        request,
        "hr/reports.html",
        {"page_title": "HR Reports - SORA ERP", "stats": stats, "types": _accepted_report_types()},
    )


def _report_page(request, report_type):
    _hr_staff(request)
    builder = REPORT_BUILDERS.get(report_type)
    if builder is None:
        return redirect("/hr/reports")
    headers, rows, summary = builder(request)
    query = request.GET.copy()
    query["type"] = report_type
    departments = _departments_map()
    return render(
        request,
        "hr/report_table.html",
        {
            "page_title": f"{_accepted_report_types()[report_type]} Report - SORA ERP",
            "report_type": report_type,
            "report_title": _accepted_report_types()[report_type],
            "headers": headers,
            "rows": rows,
            "summary": summary,
            "export_query": query.urlencode(),
            "filter_options": {
                "departments": sorted(departments.values(), key=lambda d: d.department_name),
                "leave_statuses": LeaveRequest._meta.get_field("status").choices,
                "employee_statuses": Employee._meta.get_field("employment_status").choices,
                "periods": PayrollPeriod.objects.order_by("-start_date"),
            },
            "selected": {
                "department": request.GET.get("department") or "",
                "status": request.GET.get("status") or "",
                "year": request.GET.get("year") or timezone.localdate().year,
                "month": request.GET.get("month") or timezone.localdate().month,
                "period": request.GET.get("period") or "",
            },
            "show_department": report_type in ["employees", "attendance"],
            "show_status": report_type in ["employees", "leave"],
            "show_year": report_type in ["leave", "performance", "recruitment"],
            "show_month": report_type == "attendance",
            "show_period": report_type == "payroll",
            "today": timezone.localdate(),
        },
    )


@login_required
@module_access_required("hr")
def report_employees(request):
    return _report_page(request, "employees")


@login_required
@module_access_required("hr")
def report_leave(request):
    return _report_page(request, "leave")


@login_required
@module_access_required("hr")
def report_attendance(request):
    return _report_page(request, "attendance")


@login_required
@module_access_required("hr")
def report_performance(request):
    return _report_page(request, "performance")


@login_required
@module_access_required("hr")
def report_recruitment(request):
    return _report_page(request, "recruitment")


@login_required
@module_access_required("hr")
def report_payroll(request):
    return _report_page(request, "payroll")


@login_required
@module_access_required("hr")
def report_export(request):
    _hr_staff(request)
    report_type = request.GET.get("type") or ""
    builder = REPORT_BUILDERS.get(report_type)
    if builder is None:
        messages.error(request, "Unknown report type.")
        return redirect("/hr/reports")
    headers, rows, _summary = builder(request)
    return csv_response(f"{report_type}-report.csv", headers, rows)
