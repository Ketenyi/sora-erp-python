"""Sales module views: quotes, pipeline and reports."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import Client, Invoice, InvoiceItem, Payment, Product, Quote, QuoteItem
from apps.core.permissions import get_access, module_access_required
from apps.core.services import next_sequence
from apps.sales.forms import QuoteForm, QuoteItemFormSet

PAGE_SIZE = 15
QUOTE_STATUSES = [
    ("draft", "Draft"),
    ("sent", "Sent"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("expired", "Expired"),
    ("converted", "Converted"),
]


def _scoped_quotes(request):
    access = get_access(request.user)
    queryset = Quote.objects.all()
    if access.has_grant("sales"):
        return queryset, access
    if not access.is_sales_admin:
        queryset = queryset.filter(
            Q(created_by=request.user.id) | Q(assigned_to=request.user.id)
        )
    return queryset, access


def _client_names(ids):
    return {
        client.id: client
        for client in Client.objects.filter(id__in={i for i in ids if i})
    }


def _quote_context(request, queryset):
    from django.core.paginator import Paginator

    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    if query:
        matching_clients = Client.objects.filter(company_name__icontains=query).values("id")
        queryset = queryset.filter(
            Q(quote_number__icontains=query)
            | Q(notes__icontains=query)
            | Q(client_id__in=matching_clients)
        )
    if status:
        queryset = queryset.filter(status=status)
    queryset = queryset.order_by("-created_at")
    paginator = Paginator(queryset, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    clients = _client_names([quote.client_id for quote in page_obj])
    return {
        "page_obj": page_obj,
        "quotes": page_obj.object_list,
        "clients": clients,
        "search_query": query,
        "status_filter": status,
        "status_choices": QUOTE_STATUSES,
    }


@login_required
@module_access_required("sales")
def dashboard(request):
    quotes, access = _scoped_quotes(request)
    today = timezone.localdate()
    stats = {
        "total": quotes.count(),
        "open": quotes.filter(status__in=["draft", "sent"]).count(),
        "approved": quotes.filter(status="approved").count(),
        "converted": quotes.filter(status="converted").count(),
        "value": quotes.exclude(status__in=["rejected", "expired"]).aggregate(
            total=Sum("total_amount")
        )["total"]
        or 0,
        "month": quotes.filter(
            created_at__year=today.year, created_at__month=today.month
        ).count(),
    }
    recent = list(quotes.order_by("-created_at")[:8])
    clients = _client_names([quote.client_id for quote in recent])
    pipeline = {
        status: {
            "label": label,
            "count": quotes.filter(status=status).count(),
            "value": quotes.filter(status=status).aggregate(total=Sum("total_amount"))[
                "total"
            ]
            or 0,
        }
        for status, label in QUOTE_STATUSES[:5]
    }
    return render(
        request,
        "sales/dashboard.html",
        {
            "page_title": "Sales Dashboard - SORA ERP",
            "stats": stats,
            "recent_quotes": recent,
            "clients": clients,
            "pipeline": pipeline,
            "access": access,
        },
    )


@login_required
@module_access_required("sales")
def quote_list(request):
    quotes, _ = _scoped_quotes(request)
    context = _quote_context(request, quotes)
    context["page_title"] = "Quotes - SORA ERP"
    return render(request, "sales/quotes.html", context)


def _prepare_formset(request, quote=None):
    if request.method == "POST":
        return QuoteItemFormSet(request.POST, prefix="items")
    initial = []
    if quote:
        initial = [
            {
                "product": item.product_id,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "discount": item.discount,
            }
            for item in QuoteItem.objects.filter(quote_id=quote.id).order_by("id")
        ]
    return QuoteItemFormSet(initial=initial, prefix="items")


def _quote_form_initial(quote=None):
    if not quote:
        return {}
    return {
        "client": quote.client_id,
        "assigned_to": quote.assigned_to,
        "bank_account": quote.bank_account_id,
        "issue_date": quote.issue_date,
        "valid_until": quote.valid_until,
        "status": quote.status,
        "discount_amount": quote.discount_amount,
        "tax_rate": _tax_rate_from(quote),
        "notes": quote.notes,
        "terms_conditions": quote.terms_conditions,
    }


def _tax_rate_from(quote):
    taxable = (quote.subtotal or Decimal("0")) - (quote.discount_amount or Decimal("0"))
    if taxable and quote.tax_amount:
        return (quote.tax_amount / taxable * 100).quantize(Decimal("0.01"))
    return Decimal("16")


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
        total = (quantity * unit_price * (Decimal("100") - discount) / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
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
    subtotal = sum((item["total"] for item in items), Decimal("0")).quantize(Decimal("0.01"))
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


@login_required
@module_access_required("sales")
def quote_create(request):
    if request.method == "POST":
        form = QuoteForm(request.POST)
        formset = _prepare_formset(request)
        if form.is_valid() and formset.is_valid():
            items = _collect_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to the quote.")
            else:
                subtotal, discount, tax, total = _totals(form, items)
                if not form.errors:
                    with transaction.atomic():
                        quote = Quote.objects.create(
                            quote_number=next_sequence("QUO", "quotes", "quote_number"),
                            client_id=form.cleaned_data["client"].id,
                            assigned_to=form.cleaned_data["assigned_to"].id
                            if form.cleaned_data.get("assigned_to")
                            else request.user.id,
                            issue_date=form.cleaned_data["issue_date"],
                            valid_until=form.cleaned_data["valid_until"],
                            status=form.cleaned_data["status"],
                            subtotal=subtotal,
                            discount_amount=discount,
                            tax_amount=tax,
                            total_amount=total,
                            notes=form.cleaned_data.get("notes") or "",
                            bank_account_id=form.cleaned_data["bank_account"].id
                            if form.cleaned_data.get("bank_account")
                            else None,
                            terms_conditions=form.cleaned_data.get("terms_conditions") or "",
                            created_by=request.user.id,
                        )
                        QuoteItem.objects.bulk_create(
                            [QuoteItem(quote_id=quote.id, **item) for item in items]
                        )
                    messages.success(request, f"Quote {quote.quote_number} created.")
                    return redirect(f"/sales/quotes/view/{quote.id}")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = QuoteForm()
        formset = _prepare_formset(request)
    return render(
        request,
        "sales/quote_form.html",
        {
            "page_title": "Create Quote - SORA ERP",
            "form": form,
            "formset": formset,
            "products_json": _products_json(),
            "is_edit": False,
        },
    )


@login_required
@module_access_required("sales")
def quote_edit(request, pk):
    quotes, access = _scoped_quotes(request)
    quote = get_object_or_404(quotes, pk=pk)
    if request.method == "POST":
        form = QuoteForm(request.POST)
        formset = _prepare_formset(request, quote)
        if form.is_valid() and formset.is_valid():
            items = _collect_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to the quote.")
            else:
                subtotal, discount, tax, total = _totals(form, items)
                if not form.errors:
                    with transaction.atomic():
                        quote.client_id = form.cleaned_data["client"].id
                        quote.assigned_to = (
                            form.cleaned_data["assigned_to"].id
                            if form.cleaned_data.get("assigned_to")
                            else quote.assigned_to
                        )
                        quote.issue_date = form.cleaned_data["issue_date"]
                        quote.valid_until = form.cleaned_data["valid_until"]
                        quote.status = form.cleaned_data["status"]
                        quote.subtotal = subtotal
                        quote.discount_amount = discount
                        quote.tax_amount = tax
                        quote.total_amount = total
                        quote.notes = form.cleaned_data.get("notes") or ""
                        quote.terms_conditions = form.cleaned_data.get("terms_conditions") or ""
                        quote.bank_account_id = (
                            form.cleaned_data["bank_account"].id
                            if form.cleaned_data.get("bank_account")
                            else None
                        )
                        if quote.status == "approved" and not quote.approved_at:
                            quote.approved_at = timezone.now()
                            quote.approved_by = request.user.id
                        quote.save()
                        QuoteItem.objects.filter(quote_id=quote.id).delete()
                        QuoteItem.objects.bulk_create(
                            [QuoteItem(quote_id=quote.id, **item) for item in items]
                        )
                    messages.success(request, f"Quote {quote.quote_number} updated.")
                    return redirect(f"/sales/quotes/view/{quote.id}")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = QuoteForm(initial=_quote_form_initial(quote))
        formset = _prepare_formset(request, quote)
    return render(
        request,
        "sales/quote_form.html",
        {
            "page_title": f"Edit {quote.quote_number} - SORA ERP",
            "form": form,
            "formset": formset,
            "products_json": _products_json(),
            "quote": quote,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("sales")
def quote_view(request, pk):
    quotes, access = _scoped_quotes(request)
    quote = get_object_or_404(quotes, pk=pk)
    items = QuoteItem.objects.filter(quote_id=quote.id).order_by("id")
    client = Client.objects.filter(id=quote.client_id).first()
    bank = None
    if quote.bank_account_id:
        from apps.core.models import BankAccount

        bank = BankAccount.objects.filter(id=quote.bank_account_id).first()
    return render(
        request,
        "sales/quote_view.html",
        {
            "page_title": f"{quote.quote_number} - SORA ERP",
            "quote": quote,
            "items": items,
            "client": client,
            "bank": bank,
            "access": access,
            "can_manage": access.is_sales_admin
            or quote.created_by == request.user.id
            or quote.assigned_to == request.user.id,
            "can_approve": access.can("sales", "approve")
            and quote.status in {"draft", "sent"},
            "can_convert": access.can("sales", "edit") and quote.status == "approved",
            "can_delete": access.can("sales", "delete"),
        },
    )


@login_required
@module_access_required("sales")
def quote_approve(request, pk):
    if request.method != "POST":
        return redirect(f"/sales/quotes/view/{pk}")
    quotes, access = _scoped_quotes(request)
    quote = get_object_or_404(quotes, pk=pk)
    if not access.can("sales", "approve"):
        messages.error(request, "You do not have permission to approve quotes.")
        return redirect(f"/sales/quotes/view/{quote.id}")
    if quote.status not in {"draft", "sent"}:
        messages.error(request, "Only draft or sent quotes can be approved.")
        return redirect(f"/sales/quotes/view/{quote.id}")
    quote.status = "approved"
    quote.approved_at = timezone.now()
    quote.approved_by = request.user.id
    quote.save(update_fields=["status", "approved_at", "approved_by", "updated_at"])
    messages.success(request, f"Quote {quote.quote_number} approved.")
    return redirect(f"/sales/quotes/view/{quote.id}")


@login_required
@module_access_required("sales")
def quote_delete(request, pk):
    if request.method != "POST":
        return redirect("/sales/quotes")
    quotes, access = _scoped_quotes(request)
    quote = get_object_or_404(quotes, pk=pk)
    if not access.can("sales", "delete"):
        messages.error(request, "You do not have permission to delete quotes.")
        return redirect(f"/sales/quotes/view/{quote.id}")
    with transaction.atomic():
        QuoteItem.objects.filter(quote_id=quote.id).delete()
        number = quote.quote_number
        quote.delete()
    messages.success(request, f"Quote {number} deleted.")
    return redirect("/sales/quotes")


@login_required
@module_access_required("sales")
def quote_convert(request, pk):
    if request.method != "POST":
        return redirect(f"/sales/quotes/view/{pk}")
    quotes, access = _scoped_quotes(request)
    quote = get_object_or_404(quotes, pk=pk)
    if not access.can("sales", "edit"):
        messages.error(request, "You do not have permission to convert quotes.")
        return redirect(f"/sales/quotes/view/{quote.id}")
    if quote.status != "approved":
        messages.error(request, "Only approved quotes can be converted to invoices.")
        return redirect(f"/sales/quotes/view/{quote.id}")
    if quote.converted_to_invoice:
        messages.error(request, "This quote has already been converted.")
        return redirect(f"/invoices/view/{quote.converted_to_invoice}")

    items = list(QuoteItem.objects.filter(quote_id=quote.id).order_by("id"))
    today = timezone.localdate()
    with transaction.atomic():
        invoice = Invoice.objects.create(
            invoice_number=next_sequence("INV", "invoices", "invoice_number"),
            client_id=quote.client_id,
            quote_id=quote.id,
            invoice_date=today,
            due_date=today + timedelta(days=30),
            status="sent",
            subtotal=quote.subtotal,
            discount_amount=quote.discount_amount or Decimal("0"),
            tax_amount=quote.tax_amount or Decimal("0"),
            total_amount=quote.total_amount,
            paid_amount=Decimal("0"),
            notes=quote.notes,
            bank_account_id=quote.bank_account_id,
            payment_terms="Net 30",
            created_by=request.user.id,
        )
        InvoiceItem.objects.bulk_create(
            [
                InvoiceItem(
                    invoice_id=invoice.id,
                    product_id=item.product_id,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    discount=item.discount,
                    total=item.total,
                )
                for item in items
            ]
        )
        quote.status = "converted"
        quote.converted_to_invoice = invoice.id
        quote.save(update_fields=["status", "converted_to_invoice", "updated_at"])
    messages.success(request, f"Invoice {invoice.invoice_number} created from quote.")
    return redirect(f"/invoices/view/{invoice.id}")


@login_required
@module_access_required("sales")
def pipeline(request):
    quotes, _ = _scoped_quotes(request)
    columns = []
    for status, label in QUOTE_STATUSES:
        items = list(quotes.filter(status=status).order_by("-created_at")[:25])
        columns.append(
            {
                "status": status,
                "label": label,
                "quotes": items,
                "value": quotes.filter(status=status).aggregate(total=Sum("total_amount"))[
                    "total"
                ]
                or 0,
            }
        )
    clients = _client_names([quote.client_id for column in columns for quote in column["quotes"]])
    return render(
        request,
        "sales/pipeline.html",
        {
            "page_title": "Sales Pipeline - SORA ERP",
            "columns": columns,
            "clients": clients,
        },
    )


@login_required
@module_access_required("sales")
def reports(request):
    quotes, _ = _scoped_quotes(request)
    today = timezone.localdate()
    stats = {
        "quotes": quotes.count(),
        "approved": quotes.filter(status="approved").count(),
        "converted": quotes.filter(status="converted").count(),
        "value": quotes.aggregate(total=Sum("total_amount"))["total"] or 0,
        "month": quotes.filter(
            created_at__year=today.year, created_at__month=today.month
        ).aggregate(total=Sum("total_amount"))["total"]
        or 0,
    }
    return render(
        request,
        "sales/reports.html",
        {"page_title": "Sales Reports - SORA ERP", "stats": stats},
    )


@login_required
@module_access_required("sales")
def report_sales(request):
    invoices = Invoice.objects.exclude(status="cancelled")
    monthly = list(
        invoices.annotate(month=TruncMonth("invoice_date"))
        .values("month")
        .annotate(count=Count("id"), total=Sum("total_amount"), paid=Sum("paid_amount"))
        .order_by("-month")[:12]
    )
    for row in monthly:
        if isinstance(row["month"], str):
            try:
                row["month"] = date.fromisoformat(row["month"][:10])
            except ValueError:
                pass
        row["outstanding"] = (row["total"] or Decimal("0")) - (row["paid"] or Decimal("0"))
    return render(
        request,
        "sales/report_sales.html",
        {
            "page_title": "Sales Report - SORA ERP",
            "monthly": monthly,
            "txn_count": invoices.count(),
            "total": invoices.aggregate(total=Sum("total_amount"))["total"] or 0,
            "paid": invoices.aggregate(total=Sum("paid_amount"))["total"] or 0,
        },
    )


@login_required
@module_access_required("sales")
def report_quotes(request):
    quotes, _ = _scoped_quotes(request)
    statuses = []
    for status, label in QUOTE_STATUSES:
        subset = quotes.filter(status=status)
        statuses.append(
            {
                "status": status,
                "label": label,
                "count": subset.count(),
                "value": subset.aggregate(total=Sum("total_amount"))["total"] or 0,
            }
        )
    top_clients = (
        quotes.values("client_id")
        .annotate(count=Count("id"), total=Sum("total_amount"))
        .order_by("-total")[:10]
    )
    clients = _client_names([row["client_id"] for row in top_clients])
    return render(
        request,
        "sales/report_quotes.html",
        {
            "page_title": "Quote Report - SORA ERP",
            "statuses": statuses,
            "top_clients": top_clients,
            "clients": clients,
            "total_quotes": quotes.count(),
        },
    )


@login_required
@module_access_required("sales")
def report_export(request):
    quotes, _ = _scoped_quotes(request)
    clients = _client_names([quote.client_id for quote in quotes[:1000]])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Quote Number", "Client", "Issue Date", "Valid Until", "Status", "Subtotal", "Tax", "Total"]
    )
    for quote in quotes.order_by("-created_at"):
        client = clients.get(quote.client_id)
        writer.writerow(
            [
                quote.quote_number,
                client.company_name if client else "",
                quote.issue_date,
                quote.valid_until,
                quote.status,
                quote.subtotal,
                quote.tax_amount,
                quote.total_amount,
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sales-quotes.csv"'
    return response
