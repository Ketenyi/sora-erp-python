"""CRM views: clients, vendors and relationship reporting."""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import (
    Client,
    Invoice,
    PurchaseOrder,
    Quote,
    SupplierBill,
    Vendor,
)
from apps.core.permissions import get_access, module_access_required
from apps.core.services import next_code
from apps.core.ui import csv_response, paginate
from apps.crm.forms import ClientForm, VendorForm


def _clients():
    return Client.objects.filter(deleted_at__isnull=True)


def _vendors():
    return Vendor.objects.filter(deleted_at__isnull=True)


@login_required
@module_access_required("crm", "sales")
def dashboard(request):
    clients = _clients()
    vendors = _vendors()
    today = timezone.localdate()
    stats = {
        "clients": clients.count(),
        "active_clients": clients.filter(status="active").count(),
        "new_clients": clients.filter(
            created_at__year=today.year, created_at__month=today.month
        ).count(),
        "vendors": vendors.count(),
    }
    recent_clients = list(clients.order_by("-created_at")[:8])
    recent_vendors = list(vendors.order_by("-created_at")[:5])
    top_clients = (
        Invoice.objects.exclude(status="cancelled")
        .values("client_id")
        .annotate(invoiced=Sum("total_amount"), invoices=Count("id"))
        .order_by("-invoiced")[:5]
    )
    client_map = {client.id: client for client in clients}
    for row in top_clients:
        row["client"] = client_map.get(row["client_id"])
    return render(
        request,
        "crm/dashboard.html",
        {
            "page_title": "CRM Dashboard - SORA ERP",
            "stats": stats,
            "recent_clients": recent_clients,
            "recent_vendors": recent_vendors,
            "top_clients": top_clients,
        },
    )


@login_required
@module_access_required("crm", "sales")
def client_list(request):
    queryset = _clients().order_by("company_name")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    if query:
        queryset = queryset.filter(
            Q(company_name__icontains=query)
            | Q(client_code__icontains=query)
            | Q(contact_person__icontains=query)
            | Q(email__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    page_obj = paginate(request, queryset)
    return render(
        request,
        "crm/clients.html",
        {
            "page_title": "Clients - SORA ERP",
            "page_obj": page_obj,
            "clients": page_obj.object_list,
            "search_query": query,
            "status_filter": status,
            "access": get_access(request.user),
        },
    )


@login_required
@module_access_required("crm", "sales")
def client_create(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.client_code = next_code("CLI", "clients", "client_code")
            client.save()
            messages.success(request, f"Client {client.company_name} created.")
            return redirect(f"/crm/clients/view/{client.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ClientForm()
    return render(
        request,
        "crm/client_form.html",
        {"page_title": "New Client - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("crm", "sales")
def client_edit(request, pk):
    client = get_object_or_404(_clients(), pk=pk)
    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            messages.success(request, "Client updated.")
            return redirect(f"/crm/clients/view/{client.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ClientForm(instance=client)
    return render(
        request,
        "crm/client_form.html",
        {
            "page_title": f"Edit {client.company_name} - SORA ERP",
            "form": form,
            "client": client,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("crm", "sales")
def client_view(request, pk):
    client = get_object_or_404(_clients(), pk=pk)
    quotes = Quote.objects.filter(client_id=client.id).order_by("-created_at")[:10]
    invoices = Invoice.objects.filter(client_id=client.id).order_by("-created_at")[:10]
    totals = {
        "quoted": Quote.objects.filter(client_id=client.id)
        .exclude(status__in=["rejected", "expired"])
        .aggregate(total=Sum("total_amount"))["total"]
        or 0,
        "invoiced": Invoice.objects.filter(client_id=client.id)
        .exclude(status="cancelled")
        .aggregate(total=Sum("total_amount"))["total"]
        or 0,
        "paid": Invoice.objects.filter(client_id=client.id).aggregate(
            total=Sum("paid_amount")
        )["total"]
        or 0,
    }
    totals["outstanding"] = (totals["invoiced"] or 0) - (totals["paid"] or 0)
    access = get_access(request.user)
    return render(
        request,
        "crm/client_view.html",
        {
            "page_title": f"{client.company_name} - SORA ERP",
            "client": client,
            "quotes": quotes,
            "invoices": invoices,
            "totals": totals,
            "access": access,
            "can_delete": access.can("crm", "delete"),
        },
    )


@login_required
@module_access_required("crm", "sales")
def client_delete(request, pk):
    if request.method != "POST":
        return redirect("/crm/clients")
    client = get_object_or_404(_clients(), pk=pk)
    if not get_access(request.user).can("crm", "delete"):
        messages.error(request, "You do not have permission to delete clients.")
        return redirect(f"/crm/clients/view/{client.id}")
    client.status = "inactive"
    client.deleted_at = timezone.now()
    client.save(update_fields=["status", "deleted_at", "updated_at"])
    messages.success(request, f"Client {client.company_name} archived.")
    return redirect("/crm/clients")


@login_required
@module_access_required("crm", "sales")
def client_export(request):
    clients = _clients().order_by("company_name")
    rows = [
        [
            client.client_code,
            client.company_name,
            client.contact_person or "",
            client.email or "",
            client.phone or "",
            client.city or "",
            client.country or "",
            client.status or "",
        ]
        for client in clients
    ]
    return csv_response(
        "clients.csv",
        ["Code", "Company", "Contact", "Email", "Phone", "City", "Country", "Status"],
        rows,
    )


@login_required
@module_access_required("crm", "purchasing")
def vendor_list(request):
    queryset = _vendors().order_by("company_name")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    if query:
        queryset = queryset.filter(
            Q(company_name__icontains=query)
            | Q(vendor_code__icontains=query)
            | Q(contact_person__icontains=query)
            | Q(email__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    page_obj = paginate(request, queryset)
    return render(
        request,
        "crm/vendors.html",
        {
            "page_title": "Vendors - SORA ERP",
            "page_obj": page_obj,
            "vendors": page_obj.object_list,
            "search_query": query,
            "status_filter": status,
            "access": get_access(request.user),
        },
    )


@login_required
@module_access_required("crm", "purchasing")
def vendor_create(request):
    if request.method == "POST":
        form = VendorForm(request.POST)
        if form.is_valid():
            vendor = form.save(commit=False)
            vendor.vendor_code = next_code("VEN", "vendors", "vendor_code")
            vendor.created_by = request.user.id
            vendor.save()
            messages.success(request, f"Vendor {vendor.company_name} created.")
            return redirect(f"/crm/vendors/view/{vendor.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = VendorForm()
    return render(
        request,
        "crm/vendor_form.html",
        {"page_title": "New Vendor - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("crm", "purchasing")
def vendor_edit(request, pk):
    vendor = get_object_or_404(_vendors(), pk=pk)
    if request.method == "POST":
        form = VendorForm(request.POST, instance=vendor)
        if form.is_valid():
            vendor.updated_at = timezone.now()
            form.save()
            messages.success(request, "Vendor updated.")
            return redirect(f"/crm/vendors/view/{vendor.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = VendorForm(instance=vendor)
    return render(
        request,
        "crm/vendor_form.html",
        {
            "page_title": f"Edit {vendor.company_name} - SORA ERP",
            "form": form,
            "vendor": vendor,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("crm", "purchasing")
def vendor_view(request, pk):
    vendor = get_object_or_404(_vendors(), pk=pk)
    purchase_orders = PurchaseOrder.objects.filter(vendor_id=vendor.id).order_by(
        "-order_date"
    )[:10]
    bills = SupplierBill.objects.filter(vendor_id=vendor.id).order_by("-bill_date")[:10]
    totals = {
        "ordered": PurchaseOrder.objects.filter(vendor_id=vendor.id)
        .exclude(status="cancelled")
        .aggregate(total=Sum("total_amount"))["total"]
        or 0,
        "billed": SupplierBill.objects.filter(vendor_id=vendor.id)
        .exclude(status="cancelled")
        .aggregate(total=Sum("total_amount"))["total"]
        or 0,
        "paid": SupplierBill.objects.filter(vendor_id=vendor.id).aggregate(
            total=Sum("paid_amount")
        )["total"]
        or 0,
    }
    totals["outstanding"] = (totals["billed"] or 0) - (totals["paid"] or 0)
    access = get_access(request.user)
    return render(
        request,
        "crm/vendor_view.html",
        {
            "page_title": f"{vendor.company_name} - SORA ERP",
            "vendor": vendor,
            "purchase_orders": purchase_orders,
            "bills": bills,
            "totals": totals,
            "access": access,
            "can_delete": access.can("crm", "delete"),
        },
    )


@login_required
@module_access_required("crm", "purchasing")
def vendor_delete(request, pk):
    if request.method != "POST":
        return redirect("/crm/vendors")
    vendor = get_object_or_404(_vendors(), pk=pk)
    if not get_access(request.user).can("crm", "delete"):
        messages.error(request, "You do not have permission to delete vendors.")
        return redirect(f"/crm/vendors/view/{vendor.id}")
    vendor.status = "deleted"
    vendor.deleted_at = timezone.now()
    vendor.save(update_fields=["status", "deleted_at", "updated_at"])
    messages.success(request, f"Vendor {vendor.company_name} archived.")
    return redirect("/crm/vendors")


@login_required
@module_access_required("crm", "purchasing")
def vendor_export(request):
    vendors = _vendors().order_by("company_name")
    rows = [
        [
            vendor.vendor_code,
            vendor.company_name,
            vendor.contact_person or "",
            vendor.email or "",
            vendor.phone or "",
            vendor.city or "",
            vendor.payment_terms or "",
            vendor.status or "",
        ]
        for vendor in vendors
    ]
    return csv_response(
        "vendors.csv",
        ["Code", "Company", "Contact", "Email", "Phone", "City", "Terms (days)", "Status"],
        rows,
    )


@login_required
@module_access_required("crm", "sales", "purchasing")
def reports(request):
    clients = _clients()
    vendors = _vendors()
    invoice_totals = Invoice.objects.exclude(status="cancelled").aggregate(
        total=Sum("total_amount"), paid=Sum("paid_amount")
    )
    stats = {
        "clients": clients.count(),
        "vendors": vendors.count(),
        "invoiced": invoice_totals["total"] or 0,
        "paid": invoice_totals["paid"] or 0,
    }
    stats["outstanding"] = (stats["invoiced"] or Decimal("0")) - (stats["paid"] or Decimal("0"))
    top_clients = (
        Invoice.objects.exclude(status="cancelled")
        .values("client_id")
        .annotate(invoices=Count("id"), invoiced=Sum("total_amount"))
        .order_by("-invoiced")[:10]
    )
    client_map = {client.id: client for client in clients}
    for row in top_clients:
        row["client"] = client_map.get(row["client_id"])
    return render(
        request,
        "crm/reports.html",
        {
            "page_title": "CRM Reports - SORA ERP",
            "stats": stats,
            "top_clients": top_clients,
        },
    )
