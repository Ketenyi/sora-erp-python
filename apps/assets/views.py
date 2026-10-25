"""Asset register, custody, maintenance, depreciation and endpoint agents."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.assets import agent_services
from apps.assets.forms import (
    AgentLinkForm,
    AssetCategoryForm,
    AssetForm,
    CheckInForm,
    CheckOutForm,
    DepreciationRunForm,
    EnrollmentTokenForm,
    MaintenanceCompleteForm,
    MaintenanceForm,
)
from apps.assets.models import (
    AGENT_ONLINE_WINDOW,
    AGENT_STALE_WINDOW,
    ASSET_CONDITIONS,
    ASSET_STATUSES,
    Asset,
    AssetAgent,
    AssetAgentEnrollmentToken,
    AssetAgentEvent,
    AssetAssignment,
    AssetCategory,
    AssetDepreciation,
    AssetInventorySnapshot,
    AssetMaintenance,
    AssetSoftware,
)
from apps.assets.services import (
    book_totals,
    maintenance_revert_status,
    money,
    monthly_charge,
    post_depreciation,
)
from apps.core.permissions import get_access, module_access_required
from apps.core.services import next_code
from apps.core.tenancy import tenant_context
from apps.core.ui import csv_response, paginate
from apps.project_management.services import employee_label

ZERO = Decimal("0.00")
PAGE_SIZE = 15
DEPRECIATION_PAGE_SIZE = 20
AGENT_PAGE_SIZE = 15
SOFTWARE_PAGE_SIZE = 60
CLOSED_ASSET_STATUSES = {"retired", "disposed"}
MAINTENANCE_STATUSES = dict(AssetMaintenance._meta.get_field("status").choices)
MAINTENANCE_TYPES = dict(AssetMaintenance._meta.get_field("maintenance_type").choices)
STATUS_LABELS = dict(ASSET_STATUSES)
CONDITION_LABELS = dict(ASSET_CONDITIONS)
OS_LABELS = dict(AssetAgent._meta.get_field("os_family").choices)


def _money(value) -> Decimal:
    return money(value)


def _open_assignment(asset_id):
    return (
        AssetAssignment.objects.filter(asset_id=asset_id, returned_at__isnull=True)
        .select_related("employee")
        .order_by("-assigned_at", "-id")
        .first()
    )


def _open_assignment_map(asset_ids):
    ids = {pk for pk in asset_ids if pk}
    if not ids:
        return {}
    rows = (
        AssetAssignment.objects.filter(asset_id__in=ids, returned_at__isnull=True)
        .select_related("employee")
        .order_by("assigned_at", "id")
    )
    latest = {}
    for row in rows:
        latest[row.asset_id] = row
    return latest


def _decorate_assets(rows):
    rows = list(rows)
    assignments = _open_assignment_map({row.id for row in rows})
    today = timezone.localdate()
    for asset in rows:
        asset.category_name = (
            asset.category.category_name if asset.category_id and asset.category else "Uncategorised"
        )
        assignment = assignments.get(asset.id)
        asset.open_assignment = assignment
        if asset.assigned_to_id and asset.assigned_to:
            asset.custodian_name = employee_label(asset.assigned_to)
        elif assignment:
            asset.custodian_name = assignment.custodian_name or employee_label(assignment.employee)
        else:
            asset.custodian_name = "—"
        asset.return_overdue = bool(
            assignment
            and assignment.expected_return_date
            and assignment.expected_return_date < today
        )
        asset.estimated_monthly_depreciation = monthly_charge(asset)
    return rows


def _filtered_assets(request):
    queryset = Asset.objects.select_related("category", "assigned_to").order_by("asset_code")
    query = (request.GET.get("q") or "").strip()
    category = request.GET.get("category") or ""
    status = request.GET.get("status") or ""
    condition = request.GET.get("condition") or ""
    if query:
        queryset = queryset.filter(
            Q(asset_code__icontains=query)
            | Q(name__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(model_name__icontains=query)
            | Q(manufacturer__icontains=query)
        )
    if category:
        queryset = queryset.filter(category_id=category)
    if status:
        queryset = queryset.filter(status=status)
    if condition:
        queryset = queryset.filter(condition=condition)
    return queryset, {
        "q": query,
        "category": category,
        "status": status,
        "condition": condition,
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@module_access_required("assets")
def dashboard(request):
    today = timezone.localdate()
    access = get_access(request.user)
    assets = list(Asset.objects.select_related("category", "assigned_to"))
    active_assets = [row for row in assets if row.status not in CLOSED_ASSET_STATUSES]
    totals = book_totals(assets)
    active_totals = book_totals(active_assets)
    status_counts = {key: 0 for key in STATUS_LABELS}
    for asset in assets:
        status_counts[asset.status] = status_counts.get(asset.status, 0) + 1

    category_groups: dict[int | None, dict] = {}
    for asset in assets:
        key = asset.category_id
        row = category_groups.setdefault(
            key,
            {
                "name": asset.category.category_name if asset.category_id and asset.category else "Uncategorised",
                "count": 0,
                "cost": ZERO,
                "book_value": ZERO,
            },
        )
        row["count"] += 1
        row["cost"] += asset.purchase_cost or ZERO
        row["book_value"] += asset.book_value
    category_rows = sorted(
        category_groups.values(), key=lambda row: row["book_value"], reverse=True
    )[:8]

    open_assignments = list(
        AssetAssignment.objects.filter(returned_at__isnull=True)
        .select_related("asset", "employee")
        .order_by("expected_return_date", "assigned_at")
    )
    for assignment in open_assignments:
        assignment.holder_name = assignment.custodian_name or employee_label(assignment.employee)
        assignment.return_overdue = bool(
            assignment.expected_return_date and assignment.expected_return_date < today
        )

    upcoming_maintenance = list(
        AssetMaintenance.objects.filter(
            status__in=["scheduled", "in_progress"],
            scheduled_date__gte=today,
            scheduled_date__lte=today + timedelta(days=30),
        )
        .select_related("asset")
        .order_by("scheduled_date", "id")[:6]
    )
    overdue_maintenance = AssetMaintenance.objects.filter(
        status__in=["scheduled", "in_progress"], scheduled_date__lt=today
    ).count()
    maintenance_month = AssetMaintenance.objects.filter(
        status="completed", completed_date__year=today.year, completed_date__month=today.month
    ).aggregate(total=Sum("cost"))["total"] or ZERO
    depreciation_ytd = AssetDepreciation.objects.filter(period_date__year=today.year).aggregate(
        total=Sum("amount")
    )["total"] or ZERO
    depreciable_assets = sum(
        1 for asset in active_assets if asset.depreciation_method != "none"
    )

    recent_assets = _decorate_assets(
        Asset.objects.select_related("category", "assigned_to").order_by("-created_at", "-id")[:6]
    )

    return render(
        request,
        "assets/dashboard.html",
        {
            "page_title": "Asset Management - SORA ERP",
            "stats": {
                "assets": len(assets),
                "active_assets": len(active_assets),
                "in_use": status_counts.get("in_use", 0),
                "available": status_counts.get("available", 0),
                "under_maintenance": status_counts.get("under_maintenance", 0),
                "cost": _money(totals["cost"]),
                "book_value": _money(totals["book_value"]),
                "accumulated": _money(totals["accumulated"]),
                "active_book_value": _money(active_totals["book_value"]),
                "open_assignments": len(open_assignments),
                "overdue_returns": sum(1 for row in open_assignments if row.return_overdue),
                "overdue_maintenance": overdue_maintenance,
                "maintenance_month": _money(maintenance_month),
                "depreciation_ytd": _money(depreciation_ytd),
                "depreciable_assets": depreciable_assets,
            },
            "category_rows": category_rows,
            "status_counts": status_counts,
            "status_labels": STATUS_LABELS,
            "upcoming_maintenance": upcoming_maintenance,
            "open_assignments": open_assignments[:6],
            "recent_assets": recent_assets,
            "can_create": access.can("assets", "create"),
            "can_edit": access.can("assets", "edit"),
        },
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
@login_required
@module_access_required("assets")
def register(request):
    access = get_access(request.user)
    queryset, filters = _filtered_assets(request)
    page_obj = paginate(request, queryset, PAGE_SIZE)
    assets = _decorate_assets(page_obj.object_list)
    return render(
        request,
        "assets/register.html",
        {
            "page_title": "Asset Register - SORA ERP",
            "page_obj": page_obj,
            "assets": assets,
            "categories": AssetCategory.objects.order_by("category_name"),
            "statuses": ASSET_STATUSES,
            "conditions": ASSET_CONDITIONS,
            "filters": filters,
            "can_create": access.can("assets", "create"),
            "can_edit": access.can("assets", "edit"),
        },
    )


@login_required
@module_access_required("assets")
def register_export(request):
    queryset, _filters = _filtered_assets(request)
    assets = _decorate_assets(queryset)
    rows = [
        [
            asset.asset_code,
            asset.name,
            asset.category_name,
            asset.serial_number,
            asset.location,
            asset.purchase_date.strftime("%Y-%m-%d") if asset.purchase_date else "",
            asset.purchase_cost,
            asset.accumulated_depreciation,
            asset.book_value,
            asset.get_status_display(),
            asset.get_condition_display(),
            asset.custodian_name,
        ]
        for asset in assets
    ]
    return csv_response(
        f"asset-register-{timezone.localdate():%Y%m%d}.csv",
        [
            "Asset code",
            "Name",
            "Category",
            "Serial number",
            "Location",
            "Purchase date",
            "Purchase cost",
            "Accumulated depreciation",
            "Book value",
            "Status",
            "Condition",
            "Custodian",
        ],
        rows,
    )


@login_required
@module_access_required("assets")
def asset_create(request):
    access = get_access(request.user)
    if not access.can("assets", "create"):
        messages.error(request, "You do not have permission to add assets.")
        return redirect("/assets/register")
    if request.method == "POST":
        form = AssetForm(request.POST)
        if form.is_valid():
            asset = form.save(commit=False)
            if not asset.asset_code:
                asset.asset_code = next_code("AST", "assets_register", "asset_code")
            asset.created_by = request.user.id
            asset.save()
            messages.success(request, f"Asset {asset.asset_code} registered.")
            return redirect(f"/assets/{asset.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = AssetForm()
    return render(
        request,
        "assets/asset_form.html",
        {
            "page_title": "Register Asset - SORA ERP",
            "form": form,
            "is_edit": False,
        },
    )


@login_required
@module_access_required("assets")
def asset_edit(request, asset_id):
    access = get_access(request.user)
    asset = get_object_or_404(Asset, pk=asset_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to edit assets.")
        return redirect(f"/assets/{asset.id}")
    if request.method == "POST":
        form = AssetForm(request.POST, instance=asset)
        if form.is_valid():
            form.save()
            messages.success(request, "Asset updated.")
            return redirect(f"/assets/{asset.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = AssetForm(instance=asset)
    return render(
        request,
        "assets/asset_form.html",
        {
            "page_title": f"Edit {asset.asset_code} - SORA ERP",
            "form": form,
            "asset": asset,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("assets")
def asset_detail(request, asset_id):
    access = get_access(request.user)
    asset = get_object_or_404(
        Asset.objects.select_related("category", "assigned_to"), pk=asset_id
    )
    category = asset.category
    open_assignment = _open_assignment(asset.id)
    assignments = list(
        AssetAssignment.objects.filter(asset_id=asset.id)
        .select_related("employee")
        .order_by("-assigned_at", "-id")[:10]
    )
    for assignment in assignments:
        assignment.holder_name = assignment.custodian_name or employee_label(assignment.employee)
    maintenance_rows = list(
        AssetMaintenance.objects.filter(asset_id=asset.id)
        .select_related("performed_by")
        .order_by("-scheduled_date", "-id")[:10]
    )
    depreciation_rows = list(
        AssetDepreciation.objects.filter(asset_id=asset.id).order_by("-period_date", "-id")[:12]
    )
    maintenance_cost = AssetMaintenance.objects.filter(asset_id=asset.id, status="completed").aggregate(
        total=Sum("cost")
    )["total"] or ZERO
    next_maintenance = (
        AssetMaintenance.objects.filter(
            asset_id=asset.id, status__in=["scheduled", "in_progress"]
        )
        .order_by("scheduled_date", "id")
        .first()
    )
    today = timezone.localdate()
    if open_assignment:
        open_assignment.holder_name = open_assignment.custodian_name or employee_label(
            open_assignment.employee
        )
        if open_assignment.expected_return_date:
            open_assignment.return_overdue = open_assignment.expected_return_date < today
    for entry in depreciation_rows:
        entry.book_value = money(max((asset.purchase_cost or ZERO) - entry.accumulated_total, ZERO))

    return render(
        request,
        "assets/asset_detail.html",
        {
            "page_title": f"{asset.name} - SORA ERP",
            "asset": asset,
            "category": category,
            "linked_agent": AssetAgent.objects.filter(asset_id=asset.id).first(),
            "open_assignment": open_assignment,
            "assignments": assignments,
            "maintenance_rows": maintenance_rows,
            "depreciation_rows": depreciation_rows,
            "maintenance_cost": _money(maintenance_cost),
            "next_maintenance": next_maintenance,
            "monthly_depreciation": monthly_charge(asset),
            "check_out_form": CheckOutForm(),
            "check_in_form": CheckInForm(initial={"condition_in": asset.condition}),
            "can_edit": access.can("assets", "edit"),
            "can_manage": access.can("assets", "create"),
            "can_delete": access.can("assets", "delete"),
            "is_closed": asset.status in CLOSED_ASSET_STATUSES,
        },
    )


@login_required
@module_access_required("assets")
def asset_check_out(request, asset_id):
    access = get_access(request.user)
    asset = get_object_or_404(Asset, pk=asset_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to assign assets.")
        return redirect(f"/assets/{asset.id}")
    if request.method != "POST":
        return redirect(f"/assets/{asset.id}")
    if asset.status in CLOSED_ASSET_STATUSES:
        messages.error(request, "Retired or disposed assets cannot be checked out.")
        return redirect(f"/assets/{asset.id}")
    if _open_assignment(asset.id):
        messages.error(request, "This asset is already checked out. Check it in first.")
        return redirect(f"/assets/{asset.id}")
    form = CheckOutForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please choose an employee or enter an external custodian.")
        return redirect(f"/assets/{asset.id}")
    now = timezone.now()
    AssetAssignment.objects.create(
        asset=asset,
        employee=form.cleaned_data["employee"],
        custodian_name=(form.cleaned_data["custodian_name"] or "").strip(),
        assigned_by=request.user.id,
        assigned_at=now,
        expected_return_date=form.cleaned_data["expected_return_date"],
        condition_out=form.cleaned_data["condition_out"],
        notes=form.cleaned_data["notes"],
    )
    asset.assigned_to = form.cleaned_data["employee"]
    asset.assigned_at = now
    asset.condition = form.cleaned_data["condition_out"]
    asset.status = "in_use"
    asset.save(update_fields=["assigned_to", "assigned_at", "condition", "status", "updated_at"])
    messages.success(request, f"{asset.asset_code} checked out.")
    return redirect(f"/assets/{asset.id}")


@login_required
@module_access_required("assets")
def asset_check_in(request, asset_id):
    access = get_access(request.user)
    asset = get_object_or_404(Asset, pk=asset_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to receive assets.")
        return redirect(f"/assets/{asset.id}")
    if request.method != "POST":
        return redirect(f"/assets/{asset.id}")
    assignment = _open_assignment(asset.id)
    if assignment is None:
        messages.error(request, "This asset has no open assignment.")
        return redirect(f"/assets/{asset.id}")
    form = CheckInForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please choose the condition at check-in.")
        return redirect(f"/assets/{asset.id}")
    condition = form.cleaned_data["condition_in"]
    now = timezone.now()
    assignment.returned_at = now
    assignment.condition_in = condition
    note = (form.cleaned_data["notes"] or "").strip()
    if note:
        assignment.notes = f"{assignment.notes}\nReturn: {note}".strip() if assignment.notes else note
    assignment.save(update_fields=["returned_at", "condition_in", "notes"])
    asset.assigned_to = None
    asset.assigned_at = None
    asset.condition = condition
    asset.status = "under_maintenance" if condition in {"poor", "damaged"} else "available"
    asset.save(update_fields=["assigned_to", "assigned_at", "condition", "status", "updated_at"])
    messages.success(request, f"{asset.asset_code} checked in.")
    return redirect(f"/assets/{asset.id}")


@login_required
@module_access_required("assets")
def asset_status(request, asset_id):
    access = get_access(request.user)
    asset = get_object_or_404(Asset, pk=asset_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to change asset status.")
        return redirect(f"/assets/{asset.id}")
    if request.method == "POST":
        target = request.POST.get("status") or "retired"
        if target not in {"retired", "disposed"}:
            messages.error(request, "Choose either retire or dispose.")
            return redirect(f"/assets/{asset.id}")
        if target == "disposed" and not access.can("assets", "delete"):
            messages.error(request, "You do not have permission to dispose of assets.")
            return redirect(f"/assets/{asset.id}")
        if _open_assignment(asset.id):
            messages.error(request, "Check the asset in before retiring or disposing of it.")
            return redirect(f"/assets/{asset.id}")
        asset.status = target
        asset.save(update_fields=["status", "updated_at"])
        messages.success(request, f"{asset.asset_code} marked {STATUS_LABELS.get(target, target).lower()}.")
        return redirect(f"/assets/{asset.id}")
    return render(
        request,
        "assets/asset_status.html",
        {
            "page_title": f"Retire {asset.asset_code} - SORA ERP",
            "asset": asset,
            "target": request.GET.get("status") if request.GET.get("status") in {"retired", "disposed"} else "retired",
            "status_labels": STATUS_LABELS,
        },
    )


@login_required
@module_access_required("assets")
def asset_label(request, asset_id):
    asset = get_object_or_404(Asset.objects.select_related("category"), pk=asset_id)
    return render(
        request,
        "assets/label.html",
        {
            "page_title": f"Label {asset.asset_code} - SORA ERP",
            "asset": asset,
            "label_url": request.build_absolute_uri(f"/assets/{asset.id}"),
        },
    )


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
@login_required
@module_access_required("assets")
def categories(request):
    access = get_access(request.user)
    rows = list(AssetCategory.objects.order_by("category_name"))
    counts: dict[int, dict] = {}
    for asset in Asset.objects.select_related("category"):
        if not asset.category_id:
            continue
        bucket = counts.setdefault(asset.category_id, {"count": 0, "cost": ZERO, "book": ZERO})
        bucket["count"] += 1
        bucket["cost"] += asset.purchase_cost or ZERO
        bucket["book"] += asset.book_value
    for category in rows:
        bucket = counts.get(category.id, {"count": 0, "cost": ZERO, "book": ZERO})
        category.asset_count = bucket["count"]
        category.total_cost = _money(bucket["cost"])
        category.book_value = _money(bucket["book"])
    return render(
        request,
        "assets/categories.html",
        {
            "page_title": "Asset Categories - SORA ERP",
            "categories": rows,
            "can_create": access.can("assets", "create"),
            "can_edit": access.can("assets", "edit"),
        },
    )


@login_required
@module_access_required("assets")
def category_create(request):
    access = get_access(request.user)
    if not access.can("assets", "create"):
        messages.error(request, "You do not have permission to add categories.")
        return redirect("/assets/categories")
    if request.method == "POST":
        form = AssetCategoryForm(request.POST)
        if form.is_valid():
            category = form.save(commit=False)
            if not category.category_code:
                category.category_code = next_code("CAT", "assets_categories", "category_code")
            category.save()
            messages.success(request, f"Category {category.category_name} created.")
            return redirect("/assets/categories")
        messages.error(request, "Please correct the errors below.")
    else:
        form = AssetCategoryForm()
    return render(
        request,
        "assets/category_form.html",
        {
            "page_title": "Add Asset Category - SORA ERP",
            "form": form,
            "is_edit": False,
        },
    )


@login_required
@module_access_required("assets")
def category_edit(request, category_id):
    access = get_access(request.user)
    category = get_object_or_404(AssetCategory, pk=category_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to edit categories.")
        return redirect("/assets/categories")
    if request.method == "POST":
        form = AssetCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "Category updated.")
            return redirect("/assets/categories")
        messages.error(request, "Please correct the errors below.")
    else:
        form = AssetCategoryForm(instance=category)
    return render(
        request,
        "assets/category_form.html",
        {
            "page_title": f"Edit {category.category_name} - SORA ERP",
            "form": form,
            "category": category,
            "is_edit": True,
        },
    )


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------
def _filtered_maintenance(request):
    queryset = AssetMaintenance.objects.select_related("asset", "performed_by").order_by(
        "-scheduled_date", "-id"
    )
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    kind = request.GET.get("type") or ""
    asset_id = request.GET.get("asset") or ""
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(asset__asset_code__icontains=query)
            | Q(asset__name__icontains=query)
            | Q(vendor__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    if kind:
        queryset = queryset.filter(maintenance_type=kind)
    if asset_id:
        queryset = queryset.filter(asset_id=asset_id)
    return queryset, {"q": query, "status": status, "type": kind, "asset": asset_id}


def _maintenance_stats():
    today = timezone.localdate()
    month_start = today.replace(day=1)
    rows = list(AssetMaintenance.objects.all())
    completed_month = [
        row
        for row in rows
        if row.status == "completed" and row.completed_date and row.completed_date >= month_start
    ]
    return {
        "scheduled": sum(1 for row in rows if row.status == "scheduled"),
        "in_progress": sum(1 for row in rows if row.status == "in_progress"),
        "overdue": sum(
            1
            for row in rows
            if row.status in {"scheduled", "in_progress"} and row.scheduled_date < today
        ),
        "completed_month": len(completed_month),
        "completed_month_cost": _money(sum((row.cost or ZERO) for row in completed_month)),
    }


@login_required
@module_access_required("assets")
def maintenance(request):
    access = get_access(request.user)
    queryset, filters = _filtered_maintenance(request)
    page_obj = paginate(request, queryset, PAGE_SIZE)
    rows = list(page_obj.object_list)
    today = timezone.localdate()
    for row in rows:
        row.is_overdue = bool(
            row.status in {"scheduled", "in_progress"} and row.scheduled_date < today
        )
    return render(
        request,
        "assets/maintenance.html",
        {
            "page_title": "Asset Maintenance - SORA ERP",
            "page_obj": page_obj,
            "rows": rows,
            "stats": _maintenance_stats(),
            "filters": filters,
            "maintenance_statuses": MAINTENANCE_STATUSES.items(),
            "maintenance_types": MAINTENANCE_TYPES.items(),
            "can_create": access.can("assets", "create"),
            "can_edit": access.can("assets", "edit"),
        },
    )


def _apply_maintenance_status(item):
    if item.status == "in_progress" and item.asset.status not in CLOSED_ASSET_STATUSES:
        if item.asset.status != "under_maintenance":
            item.asset.status = "under_maintenance"
            item.asset.save(update_fields=["status", "updated_at"])
    elif item.status == "completed" and item.asset.status == "under_maintenance":
        item.asset.status = maintenance_revert_status(item.asset)
        item.asset.save(update_fields=["status", "updated_at"])


@login_required
@module_access_required("assets")
def maintenance_create(request):
    access = get_access(request.user)
    if not access.can("assets", "create"):
        messages.error(request, "You do not have permission to schedule maintenance.")
        return redirect("/assets/maintenance")
    if request.method == "POST":
        form = MaintenanceForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.created_by = request.user.id
            item.save()
            _apply_maintenance_status(item)
            messages.success(request, "Maintenance scheduled.")
            return redirect("/assets/maintenance")
        messages.error(request, "Please correct the errors below.")
    else:
        initial = {}
        asset_id = request.GET.get("asset")
        if asset_id:
            initial["asset"] = asset_id
        form = MaintenanceForm(initial=initial)
    return render(
        request,
        "assets/maintenance_form.html",
        {
            "page_title": "Schedule Maintenance - SORA ERP",
            "form": form,
            "is_edit": False,
        },
    )


@login_required
@module_access_required("assets")
def maintenance_edit(request, maintenance_id):
    access = get_access(request.user)
    item = get_object_or_404(AssetMaintenance, pk=maintenance_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to edit maintenance.")
        return redirect("/assets/maintenance")
    if request.method == "POST":
        form = MaintenanceForm(request.POST, instance=item)
        if form.is_valid():
            item = form.save()
            _apply_maintenance_status(item)
            messages.success(request, "Maintenance updated.")
            return redirect("/assets/maintenance")
        messages.error(request, "Please correct the errors below.")
    else:
        form = MaintenanceForm(instance=item)
    return render(
        request,
        "assets/maintenance_form.html",
        {
            "page_title": "Edit Maintenance - SORA ERP",
            "form": form,
            "item": item,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("assets")
def maintenance_complete(request, maintenance_id):
    access = get_access(request.user)
    item = get_object_or_404(AssetMaintenance.objects.select_related("asset"), pk=maintenance_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to complete maintenance.")
        return redirect("/assets/maintenance")
    if request.method != "POST":
        if item.status in {"completed", "cancelled"}:
            messages.info(request, "This maintenance job is already closed.")
            return redirect("/assets/maintenance")
        return render(
            request,
            "assets/maintenance_complete.html",
            {
                "page_title": f"Complete {item.title} - SORA ERP",
                "item": item,
                "form": MaintenanceCompleteForm(
                    initial={
                        "completed_date": timezone.localdate(),
                        "vendor": item.vendor,
                        "cost": item.cost,
                    }
                ),
            },
        )
    if item.status == "completed":
        messages.info(request, "This maintenance job is already completed.")
        return redirect("/assets/maintenance")
    form = MaintenanceCompleteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the completion details.")
        return redirect("/assets/maintenance")
    item.completed_date = form.cleaned_data["completed_date"]
    item.status = "completed"
    if form.cleaned_data["vendor"]:
        item.vendor = form.cleaned_data["vendor"]
    if form.cleaned_data["cost"] is not None:
        item.cost = form.cleaned_data["cost"]
    note = (form.cleaned_data["notes"] or "").strip()
    if note:
        item.description = f"{item.description}\nCompletion: {note}".strip() if item.description else note
    item.save()
    _apply_maintenance_status(item)
    messages.success(request, f"Maintenance for {item.asset.asset_code} completed.")
    return redirect("/assets/maintenance")


@login_required
@module_access_required("assets")
def maintenance_cancel(request, maintenance_id):
    access = get_access(request.user)
    item = get_object_or_404(AssetMaintenance.objects.select_related("asset"), pk=maintenance_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to cancel maintenance.")
        return redirect("/assets/maintenance")
    if request.method != "POST":
        return redirect("/assets/maintenance")
    if item.status == "completed":
        messages.error(request, "Completed maintenance cannot be cancelled.")
        return redirect("/assets/maintenance")
    item.status = "cancelled"
    item.save(update_fields=["status"])
    if item.asset.status == "under_maintenance":
        remaining = (
            AssetMaintenance.objects.filter(asset_id=item.asset_id, status="in_progress")
            .exclude(pk=item.pk)
            .exists()
        )
        if not remaining:
            item.asset.status = maintenance_revert_status(item.asset)
            item.asset.save(update_fields=["status", "updated_at"])
    messages.success(request, "Maintenance cancelled.")
    return redirect("/assets/maintenance")


# ---------------------------------------------------------------------------
# Depreciation
# ---------------------------------------------------------------------------
@login_required
@module_access_required("assets")
def depreciation(request):
    access = get_access(request.user)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        year = today.year
    if not 2000 <= year <= 2100:
        year = today.year

    if request.method == "POST":
        if not access.can("assets", "edit"):
            messages.error(request, "You do not have permission to post depreciation.")
            return redirect("/assets/depreciation")
        form = DepreciationRunForm(request.POST)
        if form.is_valid():
            summary = post_depreciation(form.cleaned_data["period"], request.user.id)
            if summary["posted"]:
                messages.success(
                    request,
                    f"{summary['period_label']}: {summary['posted']} asset(s) depreciated, "
                    f"{summary['total']:.2f} posted.",
                )
            else:
                messages.info(
                    request,
                    f"{summary['period_label']}: nothing to post "
                    f"({summary['skipped_existing']} already posted, "
                    f"{summary['skipped_ineligible']} ineligible).",
                )
            return redirect(f"/assets/depreciation?year={summary['period_date'].year}")
        messages.error(request, "Choose a valid period.")
    else:
        form = DepreciationRunForm()

    entries = AssetDepreciation.objects.filter(period_date__year=year).select_related("asset").order_by(
        "-period_date", "asset__asset_code"
    )
    totals = entries.aggregate(total=Sum("amount"))
    month_rows: dict[str, dict] = {}
    for entry in entries:
        row = month_rows.setdefault(
            entry.period_label,
            {"label": entry.period_label, "period_date": entry.period_date, "count": 0, "amount": ZERO},
        )
        row["count"] += 1
        row["amount"] += entry.amount
    page_obj = paginate(request, entries, DEPRECIATION_PAGE_SIZE)
    active_assets = [row for row in Asset.objects.exclude(status__in=CLOSED_ASSET_STATUSES)]
    depreciable = sum(1 for row in active_assets if row.depreciation_method != "none")
    return render(
        request,
        "assets/depreciation.html",
        {
            "page_title": "Asset Depreciation - SORA ERP",
            "form": form,
            "year": year,
            "years": list(range(today.year, today.year - 5, -1)),
            "page_obj": page_obj,
            "entries": page_obj.object_list,
            "month_rows": sorted(month_rows.values(), key=lambda row: row["period_date"], reverse=True)[:12],
            "summary": {
                "total": _money(totals["total"] or ZERO),
                "count": entries.count(),
                "depreciable": depreciable,
                "book_value": _money(sum((row.book_value for row in active_assets), ZERO)),
                "accumulated": _money(sum((row.accumulated_depreciation or ZERO for row in active_assets), ZERO)),
            },
            "can_edit": access.can("assets", "edit"),
        },
    )


@login_required
@module_access_required("assets")
def depreciation_export(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        year = today.year
    entries = (
        AssetDepreciation.objects.filter(period_date__year=year)
        .select_related("asset")
        .order_by("period_date", "asset__asset_code")
    )
    rows = [
        [
            entry.period_date.strftime("%Y-%m-%d"),
            entry.period_label,
            entry.asset.asset_code,
            entry.asset.name,
            entry.amount,
            entry.accumulated_total,
        ]
        for entry in entries
    ]
    return csv_response(
        f"depreciation-{year}.csv",
        ["Period date", "Period", "Asset code", "Asset", "Depreciation", "Accumulated"],
        rows,
    )


# ---------------------------------------------------------------------------
# Endpoint inventory agents
# ---------------------------------------------------------------------------
def _agent_stats():
    now = timezone.now()
    online_cutoff = now - AGENT_ONLINE_WINDOW
    stale_cutoff = now - AGENT_STALE_WINDOW
    agents = AssetAgent.objects.all()
    return {
        "agents": agents.count(),
        "online": agents.filter(is_active=True, last_seen_at__gte=online_cutoff).count(),
        "stale": agents.filter(
            is_active=True,
            last_seen_at__lt=online_cutoff,
            last_seen_at__gte=stale_cutoff,
        ).count(),
        "offline": agents.filter(is_active=True)
        .filter(Q(last_seen_at__lt=stale_cutoff) | Q(last_seen_at__isnull=True))
        .count(),
        "disabled": agents.filter(is_active=False).count(),
        "unlinked": agents.filter(asset__isnull=True).count(),
        "software": AssetSoftware.objects.values("name").distinct().count(),
        "active_tokens": AssetAgentEnrollmentToken.objects.filter(
            is_revoked=False, expires_at__gt=now, use_count__lt=F("max_uses")
        ).count(),
    }


def _filtered_agents(request):
    queryset = (
        AssetAgent.objects.select_related("asset")
        .annotate(software_total=Count("software", distinct=True))
        .order_by("hostname", "id")
    )
    query = (request.GET.get("q") or "").strip()
    os_family = request.GET.get("os") or ""
    connectivity = request.GET.get("status") or ""
    linked = request.GET.get("linked") or ""
    if query:
        queryset = queryset.filter(
            Q(hostname__icontains=query)
            | Q(display_name__icontains=query)
            | Q(machine_id__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(primary_ip__icontains=query)
            | Q(asset__asset_code__icontains=query)
            | Q(asset__name__icontains=query)
        )
    if os_family in OS_LABELS:
        queryset = queryset.filter(os_family=os_family)
    now = timezone.now()
    if connectivity == "online":
        queryset = queryset.filter(
            is_active=True, last_seen_at__gte=now - AGENT_ONLINE_WINDOW
        )
    elif connectivity == "stale":
        queryset = queryset.filter(
            is_active=True,
            last_seen_at__lt=now - AGENT_ONLINE_WINDOW,
            last_seen_at__gte=now - AGENT_STALE_WINDOW,
        )
    elif connectivity == "offline":
        queryset = queryset.filter(is_active=True).filter(
            Q(last_seen_at__lt=now - AGENT_STALE_WINDOW) | Q(last_seen_at__isnull=True)
        )
    elif connectivity == "disabled":
        queryset = queryset.filter(is_active=False)
    if linked == "yes":
        queryset = queryset.filter(asset__isnull=False)
    elif linked == "no":
        queryset = queryset.filter(asset__isnull=True)
    return queryset, {
        "q": query,
        "os": os_family,
        "status": connectivity,
        "linked": linked,
    }


@login_required
@module_access_required("assets")
def agents(request):
    access = get_access(request.user)
    queryset, filters = _filtered_agents(request)
    page_obj = paginate(request, queryset, AGENT_PAGE_SIZE)
    rows = list(page_obj.object_list)
    for agent in rows:
        agent.connectivity_label = agent.connectivity.title()
    return render(
        request,
        "assets/agents.html",
        {
            "page_title": "Endpoint Agents - SORA ERP",
            "page_obj": page_obj,
            "agents": rows,
            "stats": _agent_stats(),
            "filters": filters,
            "os_labels": OS_LABELS,
            "can_manage": access.can("assets", "edit"),
        },
    )


@login_required
@module_access_required("assets")
def agent_enrollment(request):
    access = get_access(request.user)
    can_manage = access.can("assets", "edit")
    if request.method == "POST":
        if not can_manage:
            messages.error(request, "You do not have permission to issue enrolment tokens.")
            return redirect("/assets/agents")
        form = EnrollmentTokenForm(request.POST)
        if form.is_valid():
            token, raw = agent_services.issue_enrollment_token(
                label=form.cleaned_data["label"].strip(),
                expires_days=form.cleaned_data["expires_days"],
                max_uses=form.cleaned_data["max_uses"],
                notes=(form.cleaned_data["notes"] or "").strip(),
                created_by=request.user.id,
            )
            base_url = request.build_absolute_uri("/").rstrip("/")
            query = urlencode({"token": raw, "method": "portal"})
            return render(
                request,
                "assets/agent_token_created.html",
                {
                    "page_title": "Enrolment Token - SORA ERP",
                    "token": token,
                    "raw_token": raw,
                    "server_url": base_url,
                    "download_url": request.build_absolute_uri("/assets/agents/download"),
                    "onboard_url": f"{base_url}/onboard-device?{query}",
                    "bootstrap_command": (
                        f'curl -fsSL "{base_url}/api/assets/agent/bootstrap?{query}" | sudo bash'
                    ),
                    "windows_package_url": (
                        f"{base_url}/api/assets/agent/package?{query}&platform=windows"
                    ),
                    "linux_package_url": (
                        f"{base_url}/api/assets/agent/package?{query}&platform=linux"
                    ),
                    "policy": agent_services.agent_policy(),
                },
            )
        messages.error(request, "Please correct the token details below.")
    else:
        form = EnrollmentTokenForm()

    now = timezone.now()
    tokens = list(
        AssetAgentEnrollmentToken.objects.annotate(agent_count=Count("agents")).order_by(
            "-created_at", "-id"
        )[:40]
    )
    for token in tokens:
        token.status_label = {
            "active": "Active",
            "expired": "Expired",
            "revoked": "Revoked",
            "used": "Used",
        }.get(token.status, token.status.title())
    stats = {
        "active_tokens": sum(1 for token in tokens if token.is_usable),
        "uses": sum(token.use_count for token in tokens),
        "agents": AssetAgent.objects.count(),
        "expiring": sum(
            1
            for token in tokens
            if token.is_usable and token.expires_at <= now + timedelta(days=2)
        ),
    }
    return render(
        request,
        "assets/agent_enrollment.html",
        {
            "page_title": "Agent Enrolment - SORA ERP",
            "form": form,
            "tokens": tokens,
            "stats": stats,
            "can_manage": can_manage,
        },
    )


@login_required
@module_access_required("assets")
@require_POST
def agent_token_revoke(request, token_id):
    access = get_access(request.user)
    token = get_object_or_404(AssetAgentEnrollmentToken, pk=token_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to revoke enrolment tokens.")
        return redirect("/assets/agents/enrollment")
    if token.is_revoked:
        messages.info(request, "That token is already revoked.")
    else:
        agent_services.revoke_enrollment_token(token, actor_user_id=request.user.id)
        messages.success(request, f"Enrolment token “{token.label}” revoked.")
    return redirect("/assets/agents/enrollment")


@login_required
@module_access_required("assets")
def agent_detail(request, agent_id):
    access = get_access(request.user)
    agent = get_object_or_404(
        AssetAgent.objects.select_related("asset", "enrolled_via"), pk=agent_id
    )
    software_query = (request.GET.get("sw_q") or "").strip()
    software_qs = AssetSoftware.objects.filter(agent_id=agent.id)
    if software_query:
        software_qs = software_qs.filter(
            Q(name__icontains=software_query)
            | Q(publisher__icontains=software_query)
            | Q(version__icontains=software_query)
        )
    software_page = Paginator(software_qs.order_by("name", "version"), SOFTWARE_PAGE_SIZE).get_page(
        request.GET.get("sw_page")
    )
    snapshots = list(
        AssetInventorySnapshot.objects.filter(agent_id=agent.id).order_by(
            "-received_at", "-id"
        )[:10]
    )
    latest_snapshot = snapshots[0] if snapshots else None
    events = list(
        AssetAgentEvent.objects.filter(agent_id=agent.id).order_by("-created_at", "-id")[:20]
    )
    hardware = agent.hardware or {}
    disks = []
    for disk in hardware.get("disks") or []:
        row = dict(disk)
        if row.get("total_gb") is not None and row.get("free_gb") is not None:
            row["used_gb"] = round(max(float(row["total_gb"]) - float(row["free_gb"]), 0), 1)
        disks.append(row)
    network = hardware.get("network") or []
    ports = hardware.get("ports") or agent.listening_ports or []
    security = hardware.get("security") or agent.security or {}
    disk_free = agent.disk_free_gb
    stats = {
        "software": AssetSoftware.objects.filter(agent_id=agent.id).count(),
        "snapshots": AssetInventorySnapshot.objects.filter(agent_id=agent.id).count(),
        "disk_used_gb": (
            round(float(agent.disk_total_gb or 0) - (disk_free or 0), 1)
            if agent.disk_total_gb is not None
            else None
        ),
        "memory_gb": agent.memory_total_gb,
    }
    return render(
        request,
        "assets/agent_detail.html",
        {
            "page_title": f"{agent.hostname} - Endpoint Agent - SORA ERP",
            "agent": agent,
            "stats": stats,
            "hardware": hardware,
            "disks": disks,
            "network": network,
            "ports": ports,
            "security": security,
            "snapshots": snapshots,
            "latest_snapshot": latest_snapshot,
            "events": events,
            "software_page": software_page,
            "software_rows": software_page.object_list,
            "software_query": software_query,
            "link_form": AgentLinkForm(agent=agent),
            "can_manage": access.can("assets", "edit"),
            "can_delete": access.can("assets", "delete"),
        },
    )


@login_required
@module_access_required("assets")
def agent_software_export(request, agent_id):
    agent = get_object_or_404(AssetAgent, pk=agent_id)
    rows = [
        [
            row.name,
            row.version,
            row.publisher,
            row.source,
            row.installed_on.strftime("%Y-%m-%d") if row.installed_on else "",
            row.first_seen_at.strftime("%Y-%m-%d %H:%M"),
        ]
        for row in AssetSoftware.objects.filter(agent_id=agent.id).order_by("name", "version")
    ]
    return csv_response(
        f"{agent.hostname}-software.csv",
        ["Application", "Version", "Publisher", "Source", "Installed", "First seen"],
        rows,
    )


@login_required
@module_access_required("assets")
def agent_snapshot_payload(request, agent_id, snapshot_id):
    get_object_or_404(AssetAgent, pk=agent_id)
    snapshot = get_object_or_404(AssetInventorySnapshot, pk=snapshot_id, agent_id=agent_id)
    return JsonResponse(
        {
            "snapshot_id": snapshot.pk,
            "agent_id": snapshot.agent_id,
            "collected_at": snapshot.collected_at.isoformat(),
            "received_at": snapshot.received_at.isoformat(),
            "checksum": snapshot.checksum,
            "payload": snapshot.payload,
        }
    )


@login_required
@module_access_required("assets")
@require_POST
def agent_link(request, agent_id):
    access = get_access(request.user)
    agent = get_object_or_404(AssetAgent, pk=agent_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to link endpoint agents.")
        return redirect(f"/assets/agents/{agent.id}")
    form = AgentLinkForm(request.POST, agent=agent)
    if not form.is_valid():
        messages.error(request, "Choose an asset that is not already linked to another agent.")
        return redirect(f"/assets/agents/{agent.id}")
    agent_services.link_asset(agent, form.cleaned_data["asset"], actor_user_id=request.user.id)
    messages.success(request, f"{agent.hostname} linked to {form.cleaned_data['asset']}.")
    return redirect(f"/assets/agents/{agent.id}")


@login_required
@module_access_required("assets")
@require_POST
def agent_unlink(request, agent_id):
    access = get_access(request.user)
    agent = get_object_or_404(AssetAgent, pk=agent_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to unlink endpoint agents.")
        return redirect(f"/assets/agents/{agent.id}")
    if agent.asset_id:
        agent_services.unlink_asset(agent, actor_user_id=request.user.id)
        messages.success(request, "Endpoint agent unlinked from its asset.")
    return redirect(f"/assets/agents/{agent.id}")


@login_required
@module_access_required("assets")
@require_POST
def agent_toggle(request, agent_id):
    access = get_access(request.user)
    agent = get_object_or_404(AssetAgent, pk=agent_id)
    if not access.can("assets", "edit"):
        messages.error(request, "You do not have permission to change endpoint agents.")
        return redirect(f"/assets/agents/{agent.id}")
    active = request.POST.get("active") == "1"
    agent_services.set_active(agent, active, actor_user_id=request.user.id)
    messages.success(
        request, f"{agent.hostname} {'re-activated' if active else 'deactivated'}."
    )
    return redirect(f"/assets/agents/{agent.id}")


@login_required
@module_access_required("assets")
@require_POST
def agent_delete(request, agent_id):
    access = get_access(request.user)
    agent = get_object_or_404(AssetAgent, pk=agent_id)
    if not access.can("assets", "delete"):
        messages.error(request, "You do not have permission to remove endpoint agents.")
        return redirect(f"/assets/agents/{agent.id}")
    hostname = agent.hostname
    agent.delete()
    messages.success(request, f"{hostname} removed from the workspace.")
    return redirect("/assets/agents")


@never_cache
def onboard_device(request):
    """Public self-onboarding page for a device user holding a bootstrap token."""
    raw_token = (request.POST.get("token") or request.GET.get("token") or "").strip()
    token = None
    error = ""
    context = {
        "page_title": "Onboard a Device - SORA ERP",
        "raw_token_entered": bool(raw_token),
        "error": error,
        "token": None,
        "workspace_name": "",
        "bootstrap_command": "",
        "windows_package_url": "",
        "linux_package_url": "",
        "policy": agent_services.agent_policy(),
    }
    if raw_token:
        try:
            token = agent_services.validate_install_token(raw_token)
        except agent_services.AgentAPIError as exc:
            context["error"] = exc.message
        else:
            base_url = request.build_absolute_uri("/").rstrip("/")
            query = urlencode({"token": raw_token, "method": "portal"})
            with tenant_context(token.tenant):
                context["workspace_name"] = (
                    token.tenant.brand_name or token.tenant.company_name
                )
                context["policy"] = agent_services.agent_policy()
            context.update(
                {
                    "token": token,
                    "bootstrap_command": (
                        f'curl -fsSL "{base_url}/api/assets/agent/bootstrap?{query}" | sudo bash'
                    ),
                    "windows_package_url": (
                        f"{base_url}/api/assets/agent/package?{query}&platform=windows"
                    ),
                    "linux_package_url": (
                        f"{base_url}/api/assets/agent/package?{query}&platform=linux"
                    ),
                }
            )
    return render(request, "assets/onboard_device.html", context)


@login_required
@module_access_required("assets")
def agent_download(request):
    path = Path(settings.BASE_DIR) / "agent" / "sora_asset_agent.py"
    if not path.exists():
        raise Http404("The endpoint agent package is not installed.")
    return FileResponse(
        open(path, "rb"),
        as_attachment=True,
        filename="sora_asset_agent.py",
        content_type="text/x-python",
    )
