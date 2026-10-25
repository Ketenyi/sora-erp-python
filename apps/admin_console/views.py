"""Admin console: system-wide overview for administrators."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import F, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.core.models import (
    AuditLog,
    Client,
    Employee,
    Invoice,
    JournalEntry,
    Product,
    PurchaseOrder,
    Quote,
    Role,
    User,
    UserActivityLog,
    UserRole,
)
from apps.core.permissions import admin_required, get_access
from apps.core.services import get_company_settings, get_currency_symbol

MODULE_ROWS = (
    ("Human Resources", "users", "/hr/dashboard", Employee, {"status": "active"}, "employees"),
    ("Sales", "chart-line", "/sales/dashboard", Quote, {}, "quotes"),
    ("Clients", "user-friends", "/crm/clients", Client, {"deleted_at__isnull": True}, "clients"),
    ("Invoices", "file-invoice-dollar", "/invoices/dashboard", Invoice, {}, "invoices"),
    (
        "Accounts",
        "book",
        "/accounts/dashboard",
        JournalEntry,
        {"status": "posted"},
        "journal entries",
    ),
    ("Inventory", "boxes", "/inventory/dashboard", Product, {"status": "active"}, "products"),
    ("Procurement", "truck", "/purchasing/dashboard", PurchaseOrder, {}, "purchase orders"),
    ("Users", "user-shield", "/settings/users", User, {}, "users"),
)


def _money(value) -> Decimal:
    return Decimal(value or 0)


@login_required
def dashboard(request):
    access = get_access(request.user)
    if not access.is_admin:
        messages.error(request, "Administrator access is required for the admin console.")
        return redirect("/dashboard")

    company = get_company_settings()
    currency = get_currency_symbol(company)
    today = timezone.localdate()
    month_start = today.replace(day=1)
    since = timezone.now() - timedelta(days=7)

    invoices = Invoice.objects.all()
    revenue_total = _money(invoices.filter(status="paid").aggregate(t=Sum("total_amount"))["t"])
    revenue_month = _money(
        invoices.filter(
            status="paid",
            updated_at__year=today.year,
            updated_at__month=today.month,
        ).aggregate(t=Sum("total_amount"))["t"]
    )
    outstanding_totals = invoices.filter(status__in=["sent", "partial", "overdue"]).aggregate(
        total=Sum("total_amount"), paid=Sum("paid_amount")
    )
    outstanding = _money(outstanding_totals["total"]) - _money(outstanding_totals["paid"])

    stats = {
        "users": User.objects.count(),
        "active_users": User.objects.filter(status="active").count(),
        "clients": Client.objects.filter(deleted_at__isnull=True).count(),
        "employees": Employee.objects.filter(status="active").count(),
        "quotes": Quote.objects.count(),
        "invoices": invoices.count(),
        "unpaid_invoices": invoices.filter(status__in=["sent", "partial"]).count(),
        "overdue_invoices": invoices.filter(due_date__lt=today, status__in=["sent", "partial", "overdue"]).count(),
        "products": Product.objects.filter(status="active").count(),
        "revenue_total": revenue_total,
        "revenue_month": revenue_month,
        "outstanding": outstanding,
    }

    stats["low_stock"] = (
        Product.objects.filter(status="active", reorder_level__gt=0)
        .filter(current_stock__lte=F("reorder_level"))
        .count()
    )

    module_rows = []
    for label, icon, url, model, filters, noun in MODULE_ROWS:
        module_rows.append(
            {
                "label": label,
                "icon": icon,
                "url": url,
                "count": model.objects.filter(**filters).count(),
                "noun": noun,
            }
        )

    recent_audit = list(AuditLog.objects.order_by("-created_at")[:12])
    user_emails = dict(User.objects.values_list("id", "email"))
    for entry in recent_audit:
        entry.user_email = user_emails.get(entry.user_id, f"User #{entry.user_id}")

    recent_users = list(User.objects.order_by("-id")[:6])
    role_names = dict(Role.objects.values_list("id", "display_name"))
    assignments: dict[int, list[str]] = {}
    for user_id, role_id in UserRole.objects.filter(
        user_id__in=[user.id for user in recent_users]
    ).values_list("user_id", "role_id"):
        assignments.setdefault(user_id, []).append(role_names.get(role_id, str(role_id)))
    for user in recent_users:
        user.assigned_roles = assignments.get(user.id, [])

    active_today = (
        UserActivityLog.objects.filter(created_at__gte=timezone.now() - timedelta(minutes=15))
        .values("user_id")
        .distinct()
        .count()
    )

    return render(
        request,
        "admin_console/dashboard.html",
        {
            "page_title": "Admin Console - SORA ERP",
            "company": company,
            "currency_symbol": currency,
            "stats": stats,
            "module_rows": module_rows,
            "recent_audit": recent_audit,
            "recent_users": recent_users,
            "active_today": active_today,
            "week_activity": UserActivityLog.objects.filter(created_at__gte=since).count(),
            "database_vendor": "PostgreSQL" if connection.vendor == "postgresql" else connection.vendor.title(),
            "now": timezone.localtime(),
            "today": today,
            "month_start": month_start,
        },
    )


@login_required
@admin_required
def users(request):
    return redirect("/settings/users")


@login_required
@admin_required
def roles(request):
    return redirect("/settings/roles")


@login_required
@admin_required
def settings_redirect(request):
    return redirect("/settings")


@login_required
@admin_required
def system_redirect(request):
    return redirect("/settings/system")


@login_required
@admin_required
def reports(request):
    return redirect("/dashboard")
