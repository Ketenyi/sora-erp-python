"""Invoices module views: billing, payments and receivables."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import BankAccount, Client, Invoice, InvoiceItem, Payment, Product
from apps.core.permissions import get_access, module_access_required
from apps.core.services import next_sequence
from apps.core.ui import csv_response, paginate
from apps.invoices.forms import InvoiceForm, InvoiceItemFormSet, PaymentForm

PAGE_SIZE = 15
STATUS_CHOICES = [
    ("draft", "Draft"),
    ("sent", "Sent"),
    ("partial", "Partial"),
    ("paid", "Paid"),
    ("overdue", "Overdue"),
    ("cancelled", "Cancelled"),
]
OPEN_STATUSES = ["sent", "partial", "overdue"]


def _scoped_invoices(request):
    access = get_access(request.user)
    queryset = Invoice.objects.all()
    if (
        access.is_admin
        or access.is_sales_admin
        or access.is_accounts_admin
        or access.is_accounts_viewer
    ):
        return queryset, access
    return queryset.filter(created_by=request.user.id), access


def _client_map(invoices):
    ids = {invoice.client_id for invoice in invoices if invoice.client_id}
    return {
        client.id: client.company_name
        for client in Client.objects.filter(id__in=ids)
    }


def _filtered_invoices(request, queryset):
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    date_from = request.GET.get("from") or ""
    date_to = request.GET.get("to") or ""
    if query:
        matching_clients = Client.objects.filter(company_name__icontains=query).values("id")
        queryset = queryset.filter(
            Q(invoice_number__icontains=query)
            | Q(lpo_number__icontains=query)
            | Q(client_id__in=matching_clients)
        )
    if status:
        queryset = queryset.filter(status=status)
    if date_from:
        queryset = queryset.filter(invoice_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(invoice_date__lte=date_to)
    return queryset, {
        "search_query": query,
        "status_filter": status,
        "date_from": date_from,
        "date_to": date_to,
        "status_choices": STATUS_CHOICES,
    }


def _products_json():
    return [
        {
            "id": product.id,
            "name": product.name,
            "price": str(product.unit_price),
            "description": product.description or product.name,
        }
        for product in Product.objects.filter(status="active").order_by("name")
    ]


def _prepare_formset(request, invoice=None):
    if request.method == "POST":
        return InvoiceItemFormSet(request.POST, prefix="items")
    initial = []
    if invoice:
        initial = [
            {
                "product": item.product_id,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "discount": item.discount,
            }
            for item in InvoiceItem.objects.filter(invoice_id=invoice.id).order_by("id")
        ]
    return InvoiceItemFormSet(initial=initial, prefix="items")


def _collect_items(formset):
    items = []
    for form in formset:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data:
            continue
        description = (form.cleaned_data.get("description") or "").strip()
        if not description:
            continue
        quantity = form.cleaned_data.get("quantity") or Decimal("0")
        unit_price = form.cleaned_data.get("unit_price") or Decimal("0")
        discount = form.cleaned_data.get("discount") or Decimal("0")
        total = (
            quantity * unit_price * (Decimal("100") - discount) / Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        items.append(
            {
                "product_id": form.cleaned_data["product"].id
                if form.cleaned_data.get("product")
                else None,
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount": discount,
                "total": total,
            }
        )
    return items


def _totals(form, items):
    subtotal = sum((item["total"] for item in items), Decimal("0")).quantize(
        Decimal("0.01")
    )
    discount = Decimal(form.cleaned_data.get("discount_amount") or 0)
    if discount > subtotal:
        form.add_error("discount_amount", "Discount cannot exceed the subtotal.")
        discount = subtotal
    tax_rate = Decimal(form.cleaned_data.get("tax_rate") or 0)
    tax = ((subtotal - discount) * tax_rate / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total = (subtotal - discount + tax).quantize(Decimal("0.01"))
    return subtotal, discount, tax, total


def _balance(invoice):
    return (invoice.total_amount or Decimal("0")) - (invoice.paid_amount or Decimal("0"))


@login_required
@module_access_required("sales", "accounts")
def dashboard(request):
    invoices, access = _scoped_invoices(request)
    today = timezone.localdate()
    totals = invoices.aggregate(total=Sum("total_amount"), paid=Sum("paid_amount"))
    total = totals["total"] or Decimal("0")
    paid = totals["paid"] or Decimal("0")
    overdue = invoices.filter(due_date__lt=today).exclude(status__in=["paid", "cancelled"])
    stats = {
        "count": invoices.count(),
        "total": total,
        "paid": paid,
        "outstanding": total - paid,
        "overdue_count": overdue.count(),
        "overdue_value": overdue.aggregate(total=Sum("total_amount"))["total"] or Decimal("0"),
        "month_total": invoices.filter(
            invoice_date__year=today.year, invoice_date__month=today.month
        ).aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0"),
        "collection_rate": round(paid / total * 100, 1) if total else 0,
    }
    labels = dict(STATUS_CHOICES)
    status_rows = list(
        invoices.values("status")
        .annotate(count=Count("id"), total=Sum("total_amount"))
        .order_by("status")
    )
    for row in status_rows:
        row["label"] = labels.get(row["status"], row["status"] or "Unknown")
        row["paid"] = Decimal("0")
    paid_by_status = {
        row["status"]: row["paid"]
        for row in invoices.values("status").annotate(paid=Sum("paid_amount"))
    }
    for row in status_rows:
        row["paid"] = paid_by_status.get(row["status"]) or Decimal("0")
    recent = list(invoices.order_by("-created_at")[:8])
    overdue_list = list(overdue.order_by("due_date")[:5])
    return render(
        request,
        "invoices/dashboard.html",
        {
            "page_title": "Invoices Dashboard - SORA ERP",
            "stats": stats,
            "status_rows": status_rows,
            "recent_invoices": recent,
            "overdue_invoices": overdue_list,
            "clients": _client_map(recent + overdue_list),
            "access": access,
        },
    )


@login_required
@module_access_required("sales", "accounts")
def invoice_list(request):
    invoices, access = _scoped_invoices(request)
    queryset, filters = _filtered_invoices(request, invoices)
    queryset = queryset.annotate(balance=F("total_amount") - F("paid_amount")).order_by(
        "-created_at"
    )
    page_obj = paginate(request, queryset, PAGE_SIZE)
    context = {
        "page_title": "Invoices - SORA ERP",
        "page_obj": page_obj,
        "invoices": page_obj.object_list,
        "clients": _client_map(page_obj.object_list),
        "access": access,
    }
    context.update(filters)
    return render(request, "invoices/list.html", context)


@login_required
@module_access_required("sales", "accounts")
def invoice_create(request):
    if request.method == "POST":
        form = InvoiceForm(request.POST)
        formset = _prepare_formset(request)
        if form.is_valid() and formset.is_valid():
            items = _collect_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to the invoice.")
            else:
                subtotal, discount, tax, total = _totals(form, items)
                if not form.errors:
                    with transaction.atomic():
                        invoice = Invoice.objects.create(
                            invoice_number=next_sequence(
                                "INV", "invoices", "invoice_number"
                            ),
                            client_id=form.cleaned_data["client"].id,
                            invoice_date=form.cleaned_data["invoice_date"],
                            due_date=form.cleaned_data["due_date"],
                            status=form.cleaned_data["status"],
                            subtotal=subtotal,
                            discount_amount=discount,
                            tax_amount=tax,
                            total_amount=total,
                            paid_amount=Decimal("0"),
                            notes=form.cleaned_data.get("notes") or "",
                            bank_account_id=form.cleaned_data["bank_account"].id
                            if form.cleaned_data.get("bank_account")
                            else None,
                            payment_terms=form.cleaned_data.get("payment_terms") or "",
                            lpo_number=form.cleaned_data.get("lpo_number") or "",
                            created_by=request.user.id,
                        )
                        InvoiceItem.objects.bulk_create(
                            [
                                InvoiceItem(invoice_id=invoice.id, **item)
                                for item in items
                            ]
                        )
                    messages.success(
                        request, f"Invoice {invoice.invoice_number} created."
                    )
                    return redirect(f"/invoices/view/{invoice.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = InvoiceForm()
        formset = _prepare_formset(request)
    return render(
        request,
        "invoices/invoice_form.html",
        {
            "page_title": "Create Invoice - SORA ERP",
            "form": form,
            "formset": formset,
            "is_edit": False,
            "products_json": _products_json(),
        },
    )


@login_required
@module_access_required("sales", "accounts")
def invoice_edit(request, pk):
    invoices, access = _scoped_invoices(request)
    invoice = get_object_or_404(invoices, pk=pk)
    if invoice.status in {"paid", "cancelled"}:
        messages.error(request, "Paid or cancelled invoices cannot be edited.")
        return redirect(f"/invoices/view/{invoice.id}")
    if request.method == "POST":
        form = InvoiceForm(request.POST, instance=invoice)
        formset = _prepare_formset(request, invoice)
        if form.is_valid() and formset.is_valid():
            items = _collect_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to the invoice.")
            else:
                subtotal, discount, tax, total = _totals(form, items)
                if not form.errors:
                    with transaction.atomic():
                        invoice.client_id = form.cleaned_data["client"].id
                        invoice.invoice_date = form.cleaned_data["invoice_date"]
                        invoice.due_date = form.cleaned_data["due_date"]
                        invoice.status = form.cleaned_data["status"]
                        invoice.subtotal = subtotal
                        invoice.discount_amount = discount
                        invoice.tax_amount = tax
                        invoice.total_amount = total
                        invoice.notes = form.cleaned_data.get("notes") or ""
                        invoice.payment_terms = (
                            form.cleaned_data.get("payment_terms") or ""
                        )
                        invoice.lpo_number = form.cleaned_data.get("lpo_number") or ""
                        invoice.bank_account_id = (
                            form.cleaned_data["bank_account"].id
                            if form.cleaned_data.get("bank_account")
                            else None
                        )
                        invoice.save()
                        InvoiceItem.objects.filter(invoice_id=invoice.id).delete()
                        InvoiceItem.objects.bulk_create(
                            [
                                InvoiceItem(invoice_id=invoice.id, **item)
                                for item in items
                            ]
                        )
                    messages.success(request, "Invoice updated.")
                    return redirect(f"/invoices/view/{invoice.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = InvoiceForm(instance=invoice)
        formset = _prepare_formset(request, invoice)
    return render(
        request,
        "invoices/invoice_form.html",
        {
            "page_title": f"Edit {invoice.invoice_number} - SORA ERP",
            "form": form,
            "formset": formset,
            "invoice": invoice,
            "is_edit": True,
            "products_json": _products_json(),
        },
    )


@login_required
@module_access_required("sales", "accounts")
def invoice_view(request, pk):
    invoices, access = _scoped_invoices(request)
    invoice = get_object_or_404(invoices, pk=pk)
    client = Client.objects.filter(pk=invoice.client_id).first()
    items = InvoiceItem.objects.filter(invoice_id=invoice.id).order_by("id")
    bank = (
        BankAccount.objects.filter(pk=invoice.bank_account_id).first()
        if invoice.bank_account_id
        else None
    )
    payments = Payment.objects.filter(invoice_id=invoice.id).order_by(
        "-payment_date", "-id"
    )
    today = timezone.localdate()
    can_edit = (
        access.can("sales", "edit") or access.can("accounts", "edit")
    ) and invoice.status not in {"paid", "cancelled"}
    return render(
        request,
        "invoices/invoice_view.html",
        {
            "page_title": f"{invoice.invoice_number} - SORA ERP",
            "invoice": invoice,
            "client": client,
            "items": items,
            "bank": bank,
            "payments": payments,
            "balance": _balance(invoice),
            "is_overdue": invoice.due_date < today
            and invoice.status not in {"paid", "cancelled"},
            "can_edit": can_edit,
            "can_pay": can_edit and _balance(invoice) > 0,
            "access": access,
        },
    )


@login_required
@module_access_required("sales", "accounts")
def invoice_pay(request, pk):
    invoices, access = _scoped_invoices(request)
    invoice = get_object_or_404(invoices, pk=pk)
    balance = _balance(invoice)
    if invoice.status == "cancelled":
        messages.error(request, "Cancelled invoices cannot receive payments.")
        return redirect(f"/invoices/view/{invoice.id}")
    if balance <= 0:
        messages.info(request, "This invoice is already fully paid.")
        return redirect(f"/invoices/view/{invoice.id}")
    if not (access.can("sales", "edit") or access.can("accounts", "edit")):
        messages.error(request, "You do not have permission to record payments.")
        return redirect(f"/invoices/view/{invoice.id}")

    if request.method == "POST":
        form = PaymentForm(request.POST, balance=balance)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            with transaction.atomic():
                Payment.objects.create(
                    payment_number=next_sequence("PAY", "payments", "payment_number"),
                    invoice_id=invoice.id,
                    amount=amount,
                    payment_date=form.cleaned_data["payment_date"],
                    payment_method=form.cleaned_data["payment_method"],
                    reference_number=form.cleaned_data.get("reference_number") or "",
                    notes=form.cleaned_data.get("notes") or "",
                )
                new_paid = (invoice.paid_amount or Decimal("0")) + amount
                invoice.paid_amount = new_paid
                invoice.status = (
                    "paid" if new_paid >= invoice.total_amount else "partial"
                )
                invoice.save(update_fields=["paid_amount", "status", "updated_at"])
            messages.success(request, f"Payment of {amount:,.2f} recorded.")
            return redirect(f"/invoices/view/{invoice.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = PaymentForm(balance=balance, initial={"amount": balance})
    return render(
        request,
        "invoices/pay.html",
        {
            "page_title": f"Record Payment - {invoice.invoice_number}",
            "invoice": invoice,
            "client": Client.objects.filter(pk=invoice.client_id).first(),
            "form": form,
            "balance": balance,
        },
    )


@login_required
@module_access_required("sales", "accounts")
def receivables(request):
    invoices, access = _scoped_invoices(request)
    today = timezone.localdate()
    queryset = (
        invoices.filter(status__in=OPEN_STATUSES)
        .filter(paid_amount__lt=F("total_amount"))
        .annotate(outstanding=F("total_amount") - F("paid_amount"))
        .order_by("due_date")
    )
    totals = {
        "outstanding": queryset.aggregate(total=Sum("outstanding"))["total"]
        or Decimal("0"),
        "count": queryset.count(),
    }
    buckets = {
        "current": Decimal("0"),
        "1_30": Decimal("0"),
        "31_60": Decimal("0"),
        "61_90": Decimal("0"),
        "over_90": Decimal("0"),
    }
    for row in queryset:
        days = (today - row.due_date).days
        if days <= 0:
            buckets["current"] += row.outstanding
        elif days <= 30:
            buckets["1_30"] += row.outstanding
        elif days <= 60:
            buckets["31_60"] += row.outstanding
        elif days <= 90:
            buckets["61_90"] += row.outstanding
        else:
            buckets["over_90"] += row.outstanding
    page_obj = paginate(request, queryset, 20)
    for invoice in page_obj.object_list:
        invoice.days_overdue = max((today - invoice.due_date).days, 0)
    return render(
        request,
        "invoices/receivables.html",
        {
            "page_title": "Receivables - SORA ERP",
            "page_obj": page_obj,
            "invoices": page_obj.object_list,
            "clients": _client_map(page_obj.object_list),
            "totals": totals,
            "buckets": buckets,
            "today": today,
            "access": access,
        },
    )


@login_required
@module_access_required("sales", "accounts")
def reports(request):
    invoices, _ = _scoped_invoices(request)
    today = timezone.localdate()
    start = today - timedelta(days=365)
    monthly = list(
        invoices.filter(invoice_date__gte=start)
        .annotate(month=TruncMonth("invoice_date"))
        .values("month")
        .annotate(count=Count("id"), total=Sum("total_amount"), paid=Sum("paid_amount"))
        .order_by("-month")
    )
    for row in monthly:
        row["outstanding"] = (row["total"] or Decimal("0")) - (
            row["paid"] or Decimal("0")
        )
    labels = dict(STATUS_CHOICES)
    status_rows = list(
        invoices.values("status")
        .annotate(count=Count("id"), total=Sum("total_amount"), paid=Sum("paid_amount"))
        .order_by("-total")
    )
    for row in status_rows:
        row["label"] = labels.get(row["status"], row["status"] or "Unknown")
        row["outstanding"] = (row["total"] or Decimal("0")) - (
            row["paid"] or Decimal("0")
        )
    totals = invoices.aggregate(total=Sum("total_amount"), paid=Sum("paid_amount"))
    stats = {
        "total": totals["total"] or Decimal("0"),
        "paid": totals["paid"] or Decimal("0"),
    }
    stats["outstanding"] = stats["total"] - stats["paid"]
    stats["collection_rate"] = (
        round(stats["paid"] / stats["total"] * 100, 1) if stats["total"] else 0
    )
    top_clients = list(
        invoices.exclude(status="cancelled")
        .values("client_id")
        .annotate(count=Count("id"), total=Sum("total_amount"), paid=Sum("paid_amount"))
        .order_by("-total")[:10]
    )
    clients = {client.id: client for client in Client.objects.all()}
    for row in top_clients:
        row["client"] = clients.get(row["client_id"])
        row["outstanding"] = (row["total"] or Decimal("0")) - (
            row["paid"] or Decimal("0")
        )
    return render(
        request,
        "invoices/reports.html",
        {
            "page_title": "Invoice Reports - SORA ERP",
            "monthly": monthly,
            "status_rows": status_rows,
            "stats": stats,
            "top_clients": top_clients,
        },
    )


@login_required
@module_access_required("sales", "accounts")
def invoice_export(request):
    invoices, _ = _scoped_invoices(request)
    queryset, _filters = _filtered_invoices(request, invoices)
    queryset = queryset.order_by("-created_at")
    clients = _client_map(queryset)
    rows = [
        [
            invoice.invoice_number,
            clients.get(invoice.client_id, ""),
            invoice.invoice_date.isoformat(),
            invoice.due_date.isoformat(),
            invoice.status or "",
            invoice.subtotal,
            invoice.tax_amount,
            invoice.total_amount,
            invoice.paid_amount,
            _balance(invoice),
        ]
        for invoice in queryset
    ]
    return csv_response(
        "invoices.csv",
        [
            "Invoice",
            "Client",
            "Invoice Date",
            "Due Date",
            "Status",
            "Subtotal",
            "Tax",
            "Total",
            "Paid",
            "Balance",
        ],
        rows,
    )
